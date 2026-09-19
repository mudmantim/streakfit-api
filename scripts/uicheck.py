#!/usr/bin/env python3
"""End-to-end UI checks: the product behaviours pytest cannot see.

Why this exists
---------------
`scripts/verification/` proves the API is reachable and correct; pytest proves
the backend logic. Neither can see whether the *app* does the right thing, and
that is exactly where the worst bugs lived:

  * Rickie stopped reacting on day 2, because the reaction was gated on XP and
    a returning user earns none on the first four taps. Every API response was
    correct. Every test passed. The companion was simply silent.
  * A guest completing the identical mission got no celebration at all.

Both were invisible to the whole existing test suite. These checks drive the
real UI in headless Chrome and assert on what a person would actually see.

Running it
----------
    python scripts/uicheck.py                    # against http://localhost:5000
    python scripts/uicheck.py --base-url http://localhost:5001

Requires Chrome and a running local server. Deliberately NOT part of
`scripts/verify_all.py`: that suite is standard-library-only and safe to point
at production, while this one needs a browser and writes directly to the
database to simulate a returning user. It refuses any non-local URL.

Exit codes: 0 all passed, 1 a check failed, 2 the environment was not usable
(no Chrome, server down) and nothing was actually verified.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

passes: list[str] = []
failures: list[str] = []


def ok(msg: str) -> None:
    passes.append(msg)
    print(f"  ✓ {msg}")


def bad(msg: str) -> None:
    failures.append(msg)
    print(f"  ✗ {msg}")


def check(condition: bool, msg: str, detail: str = "") -> bool:
    if condition:
        ok(msg)
    else:
        bad(f"{msg}{(' — ' + detail) if detail else ''}")
    return bool(condition)


# ── Minimal CDP client (stdlib only: no pip install for a dev tool) ─────────

class _WS:
    def __init__(self, url: str):
        rest = url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)))
        self.sock.settimeout(30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if accept not in buf.decode(errors="replace"):
            raise RuntimeError("websocket handshake rejected by Chrome")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("Chrome closed the debugging socket")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, payload: str) -> None:
        data = payload.encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        while True:
            b0, b1 = self._exact(2)
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._exact(8))[0]
            payload = self._exact(ln)
            if (b0 & 0x0F) == 0x8:
                raise RuntimeError("Chrome closed the connection")
            if (b0 & 0x0F) in (0x1, 0x2):
                return payload.decode(errors="replace")


class Browser:
    """A phone-sized headless Chrome, driven over CDP."""

    def __init__(self, width: int = 390, height: int = 844, port: int = 9333):
        for binary in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            if _which(binary):
                break
        else:
            raise EnvironmentError("no Chrome/Chromium binary found on PATH")
        self.proc = subprocess.Popen(
            [
                binary, "--headless=new", f"--remote-debugging-port={port}",
                f"--user-data-dir=/tmp/streakfit-uicheck-{port}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-gpu", f"--window-size={width},{height}",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        target = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as r:
                    pages = [t for t in json.load(r) if t.get("type") == "page"]
                if pages:
                    target = pages[0]
                    break
            except Exception:
                time.sleep(0.2)
        if not target:
            raise EnvironmentError("Chrome did not expose a debugging target")
        self.ws = _WS(target["webSocketDebuggerUrl"])
        self._id = 0
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=width, height=height, deviceScaleFactor=2, mobile=True,
        )

    def call(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True, userGesture=True)
        if r.get("exceptionDetails"):
            desc = r["exceptionDetails"].get("exception", {}).get("description", "")
            raise RuntimeError(f"page JS threw: {desc or r['exceptionDetails'].get('text')}")
        return r.get("result", {}).get("value")

    def goto(self, url: str, wait: float = 2.5) -> None:
        self.call("Page.navigate", url=url)
        time.sleep(wait)

    def reset_storage(self) -> None:
        """Clear the service worker and caches so a check never runs against a
        previous version's cached app.js — which silently invalidates results."""
        self.js(
            "(async()=>{const rs=await navigator.serviceWorker.getRegistrations();"
            "for(const r of rs) await r.unregister();"
            "const ks=await caches.keys(); for(const k of ks) await caches.delete(k);"
            "try{localStorage.clear();}catch(e){} return 1;})()"
        )

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def _which(binary: str) -> bool:
    from shutil import which
    return which(binary) is not None


# ── Test-account plumbing ──────────────────────────────────────────────────

def _api(base: str, path: str, method: str = "GET", token: str | None = None, body=None):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body else None
    try:
        with urllib.request.urlopen(req, data, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"__status__": e.code, "body": e.read().decode(errors="replace")[:200]}


def make_user(app, tag: str, seed_days: list[int] | None = None):
    """Create a user directly and mint a token.

    Direct creation rather than POST /api/register on purpose: registration is
    rate limited to 5/min, and a check that creates several accounts would
    otherwise fail on the limiter rather than on anything it meant to test.
    """
    from flask_jwt_extended import create_access_token
    from werkzeug.security import generate_password_hash

    username = f"uicheck_{tag}_{int(time.time() * 1000) % 1000000}"
    from app import DailyCompletion, User, db

    with app.app_context():
        db.session.add(User(username=username, password_hash=generate_password_hash("x")))
        db.session.commit()
        row = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
        token = create_access_token(identity=str(row.id), expires_delta=dt.timedelta(hours=2))
        if seed_days:
            keys = ["knee_push_up", "bodyweight_squat", "superman", "childs_pose", "jumping_jack"]
            for off in seed_days:
                when = dt.date.today() - dt.timedelta(days=off)
                for key in keys:
                    db.session.add(DailyCompletion(user_id=row.id, date=when, exercise_key=key))
            db.session.commit()
    return username, token


def seed_today_keys(app, username: str, base: str, token: str, days_ago: int = 3):
    """Mark today's actual mission keys as done on an earlier day, so the user
    is a genuine returning user for whom nothing today is new.

    Three days back, not one: today's mission now actively avoids repeating
    what was done YESTERDAY, so seeding into yesterday would change the mission
    and the keys would no longer be the ones on screen. What this check is
    about is repeat completions paying, which only needs "done before"."""
    from app import DailyCompletion, User, db

    daily = _api(base, "/api/daily", token=token)
    keys = [e["key"] for e in daily["exercises"]]
    with app.app_context():
        row = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
        when = dt.date.today() - dt.timedelta(days=days_ago)
        for key in keys:
            db.session.add(DailyCompletion(user_id=row.id, date=when, exercise_key=key))
        db.session.commit()
    return keys


# ── The checks ─────────────────────────────────────────────────────────────

def tap_and_read(b: Browser, settle: float = 1.1):
    """Tap the first available 'I did this' and report what the user then sees."""
    b.js(
        "(()=>{const bs=[...document.querySelectorAll('button')]"
        ".filter(b=>b.textContent.trim()==='I did this');"
        "if(bs.length) bs[0].click(); return bs.length;})()"
    )
    time.sleep(settle)
    return {
        "toast": bool(b.js("(()=>{const t=document.getElementById('rickie-reaction');"
                           " return !!t && !t.hidden;})()")),
        "line": b.js("document.getElementById('rickie-reaction-line').textContent") or "",
        "xp": b.js("(()=>{const p=document.getElementById('rickie-reaction-progress');"
                   " return p.hidden ? '' : p.textContent;})()") or "",
    }


def go_to_pane(b: Browser, name: str) -> bool:
    """Switch panes the way a person does — by tapping the nav button.

    Not by calling showPane(): these checks exist to prove a surface is
    REACHABLE, and driving the router directly would keep passing even if the
    button that gets you there had stopped working. That is the exact failure
    this harness was built for.
    """
    clicked = b.js(
        "(()=>{const b=[...document.querySelectorAll('.pane-nav-btn')]"
        f".find(x=>x.dataset.paneTarget==='{name}' && !x.hidden);"
        " if(!b) return 0; b.click(); return 1;})()"
    )
    time.sleep(0.6)
    return bool(clicked)


def check_returning_user_is_acknowledged(b: Browser, base: str, app) -> None:
    print("\nA returning user (day 2) — the bug that started all of this")
    username, token = make_user(app, "day2")
    seed_today_keys(app, username, base, token)

    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    results = []
    for _ in range(4):
        results.append(tap_and_read(b))
        time.sleep(3.9)

    silent = [i + 1 for i, r in enumerate(results) if not r["toast"]]
    check(not silent, "Rickie reacts to every tap, not just the paying ones",
          f"silent on tap(s) {silent}")

    unpaid = [i + 1 for i, r in enumerate(results) if "+5 XP" not in r["xp"]]
    check(not unpaid, "each repeat completion visibly pays 5 XP",
          f"no XP shown on tap(s) {unpaid}: {[r['xp'] for r in results]}")

    check(len({r["line"] for r in results}) > 1,
          "Rickie's lines vary rather than repeating one string",
          f"saw only {set(r['line'] for r in results)}")

    final = tap_and_read(b)
    check(final["toast"] and "+45 XP" in final["xp"],
          "finishing the mission pays more than the taps that led to it",
          f"got {final['xp']!r}")


def check_first_mission_celebration(b: Browser, base: str, app) -> None:
    """The biggest moment of a first day has to actually be on screen."""
    print("\nA first mission — the payoff Olivia is here for")
    _, token = make_user(app, "firstday")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    for _ in range(4):
        tap_and_read(b)
        time.sleep(4.6)

    b.js("""window.__tl = []; window.__t0 = Date.now();
        window.__iv = setInterval(() => {
            const t = document.getElementById('rickie-reaction');
            window.__tl.push([Date.now() - window.__t0, t.hidden ? null :
                document.getElementById('rickie-reaction-line').textContent,
                document.getElementById('rickie-reaction-progress').hidden ? '' :
                document.getElementById('rickie-reaction-progress').textContent]);
        }, 150);""")
    b.js("(()=>{const bs=[...document.querySelectorAll('button')]"
         ".filter(b=>b.textContent.trim()==='I did this'); if(bs.length) bs[0].click(); return 1;})()")
    time.sleep(9.0)
    b.js("clearInterval(window.__iv)")
    timeline = json.loads(b.js("JSON.stringify(window.__tl)") or "[]")

    # How long the completion toast — the one carrying the XP — stayed up.
    with_xp = [ms for ms, line, xp in timeline if xp and "XP" in xp]
    held_ms = (max(with_xp) - min(with_xp)) if len(with_xp) > 1 else 0
    check(held_ms >= 3000,
          "the mission-complete celebration stays on screen long enough to read",
          f"the +XP line was visible for only {held_ms}ms before something replaced it")

    lines = {line for _, line, _ in timeline if line}
    check(len(lines) >= 2,
          "the milestone gets its own moment rather than stomping the completion",
          f"only saw: {lines}")


def check_guest_gets_the_celebration(b: Browser, base: str) -> None:
    print("\nGuest mode — the moment that has to earn the signup")
    b.goto(base + "/", wait=1.2)
    b.reset_storage()
    b.goto(base + "/", wait=2.5)
    b.js("handleGuestMode()")
    time.sleep(2.5)

    seen = []
    for _ in range(5):
        seen.append(tap_and_read(b))
        time.sleep(3.9)

    check(all(r["toast"] for r in seen), "a guest gets a Rickie line on every tap",
          f"silent on {[i + 1 for i, r in enumerate(seen) if not r['toast']]}")
    check(not any(r["xp"] for r in seen),
          "a guest is never shown XP the app is not keeping for them",
          f"showed {[r['xp'] for r in seen if r['xp']]}")
    banner = b.js("(()=>{const e=document.querySelector('.guest-complete-banner');"
                  " return e ? e.innerText : '';})()") or ""
    check("Day 1 Complete" in banner, "the guest completion banner appears at 5/5",
          f"banner was {banner[:60]!r}")


def check_team_witness(b: Browser, base: str, app) -> None:
    print("\nTeams — can a parent see that their kid moved today?")
    parent, parent_token = make_user(app, "parent")
    kid, kid_token = make_user(app, "kid", seed_days=[2, 1])

    created = _api(base, "/api/teams", "POST", parent_token, {"name": "UICheck Family"})
    team = created.get("team")
    if not team:
        bad(f"could not create a team to check ({created})")
        return
    _api(base, f"/api/teams/{team['id']}/join", "POST", kid_token, {"code": team["invite_code"]})

    daily = _api(base, "/api/daily", token=kid_token)
    for ex in daily["exercises"]:
        _api(base, f"/api/daily/{ex['key']}/complete", "POST", kid_token)

    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(parent_token)})")
    b.goto(base + "/", wait=3.0)

    check(go_to_pane(b, "team"), "the Team tab appears for someone who has a team")
    cards = b.js("JSON.stringify([...document.querySelectorAll('.team-card')].map(e=>e.innerText))")
    check("moved today" in (cards or ""), "the team card carries a same-day witness line",
          f"cards were {cards}")

    b.js("(()=>{const btn=[...document.querySelectorAll('button')]"
         ".find(b=>b.textContent.trim()==='Open'); if(btn) btn.click(); return 1;})()")
    time.sleep(2.5)

    roster = b.js("(()=>{const e=document.querySelector('.team-roster');"
                  " return e ? e.innerText : '';})()") or ""
    check("Moved today" in roster, "the roster shows who has moved today",
          f"roster was {roster[:120]!r}")
    check("days" in roster or "day" in roster, "the roster shows each member's streak",
          f"roster was {roster[:120]!r}")
    check(kid in roster, "the kid appears on the roster their parent is reading")

    history = b.js("(()=>{const e=document.querySelector('.team-moments-body');"
                   " return e ? e.innerText : '';})()") or ""
    check(bool(history.strip()) and "Looking back" not in history,
          "team history renders (the moments endpoint has a caller at last)",
          f"history was {history[:120]!r}")

    campfire = b.js("(()=>{const e=document.querySelector('.team-campfire-section');"
                    " return e ? e.innerText : '';})()") or ""
    check("to reach" in campfire, "the campfire shows progress toward its next stage",
          f"campfire was {campfire[:120]!r}")


def check_photo_sharing(b: Browser, base: str, app) -> None:
    """The Olivia test: take a photo, filter it, send it, family sees it."""
    print("\nTeam photos — can Olivia send her family a goofy picture?")
    parent, parent_token = make_user(app, "pphoto_parent")
    kid, kid_token = make_user(app, "pphoto_kid")

    created = _api(base, "/api/teams", "POST", parent_token, {"name": "UICheck Photos"})
    team = created.get("team")
    if not team:
        bad(f"could not create a team to check photos ({created})")
        return
    _api(base, f"/api/teams/{team['id']}/join", "POST", kid_token,
         {"code": team["invite_code"]})

    def open_team(token):
        b.goto(base + "/", wait=1.0)
        b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
        b.goto(base + "/", wait=3.0)
        go_to_pane(b, "team")
        b.js("(()=>{const x=[...document.querySelectorAll('button')]"
             ".find(e=>e.textContent.trim()==='Open'); if(x) x.click(); return 1;})()")
        time.sleep(2.5)

    open_team(kid_token)
    check(bool(b.js("!!document.querySelector('.team-panel-photo-btn')")),
          "there is a camera button in the team panel")
    capture = b.js("(()=>{const i=document.querySelector('.team-panel-input-row input[type=file]');"
                   " return i ? (i.getAttribute('capture')||'') + '|' + (i.accept||'') : '';})()")
    check("environment" in (capture or "") and "image" in (capture or ""),
          "the camera opens directly on a phone rather than a file browser",
          f"input attrs: {capture!r}")

    # Hand the composer a file the way the picker would.
    b.js("""window.__f = (async () => {
        const cv = document.createElement('canvas'); cv.width = 320; cv.height = 240;
        const x = cv.getContext('2d'); x.fillStyle = '#4338ca'; x.fillRect(0,0,320,240);
        const blob = await new Promise(r => cv.toBlob(r, 'image/jpeg', 0.85));
        return new File([blob], 'shot.jpg', {type: 'image/jpeg'});
    })()""")
    time.sleep(0.5)
    b.js("window.__f.then(f => openPhotoComposer(f))")
    time.sleep(3.0)

    check(bool(b.js("!!document.querySelector('.photo-composer')")),
          "choosing a photo opens the composer")

    painted = b.js("""(() => {const cv = document.querySelector('.photo-composer-canvas');
        if (!cv || !cv.width) return 0;
        const d = cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
        let n = 0; for (let i=0;i<d.length;i+=400) if (d[i+3] > 0) n++;
        return n;})()""")
    check((painted or 0) > 0, "the composer previews the actual photo",
          "the preview canvas is blank")

    chips = json.loads(b.js("JSON.stringify([...document.querySelectorAll('.photo-filter-chip')]"
                            ".map(e => e.innerText.trim()))") or "[]")
    check(len(chips) >= 5, f"filters are offered ({len(chips)} of them)")
    check(any("\U0001F512" in c or "\U0001F330" in c for c in chips),
          "locked filters are shown with what they cost, not hidden",
          f"chips: {chips}")

    before = b.js("""(() => {const cv = document.querySelector('.photo-composer-canvas');
        return cv.toDataURL('image/jpeg', 0.5).length;})()""")
    b.js("(()=>{const c=[...document.querySelectorAll('.photo-filter-chip')]"
         ".find(e=>e.innerText.trim().startsWith('Rickie Photobomb')); if(c) c.click(); return 1;})()")
    time.sleep(1.8)
    after = b.js("""(() => {const cv = document.querySelector('.photo-composer-canvas');
        return cv.toDataURL('image/jpeg', 0.5).length;})()""")
    check(before != after, "picking a filter visibly changes the photo",
          "the preview is identical before and after applying a filter")

    b.js("(()=>{const i=document.querySelector('.photo-caption-input');"
         " i.value='look what I did'; return 1;})()")
    b.js("(()=>{const s=document.querySelector('.photo-send-btn'); if(s) s.click(); return 1;})()")
    time.sleep(4.5)

    check(not b.js("!!document.querySelector('.photo-composer')"),
          "the composer closes once the photo is sent")

    open_team(parent_token)
    # A real viewer taps through to the photos; the pane is a tab now.
    b.js("(()=>{const t=[...document.querySelectorAll('.team-panel-tab')]"
         ".find(e=>e.textContent.indexOf('Photos') !== -1); if(t) t.click(); return 1;})()")
    for _ in range(12):
        time.sleep(0.5)
        if b.js("[...document.querySelectorAll('.team-photo-img')]"
                ".filter(i => i.complete && i.naturalWidth > 0).length"):
            break
    seen = json.loads(b.js("""(() => {const imgs = [...document.querySelectorAll('.team-photo-img')];
        return JSON.stringify({count: imgs.length,
            loaded: imgs.filter(i => i.complete && i.naturalWidth > 0).length,
            blob: imgs.length ? imgs[0].src.startsWith('blob:') : false,
            caption: (document.querySelector('.team-photo-caption') || {}).textContent || ''});})()""")
        or "{}")
    check(seen.get("count", 0) >= 1, "the rest of the family can see the photo")
    check(seen.get("loaded", 0) >= 1, "the photo actually renders for them",
          f"{seen}")
    check(seen.get("blob") is True,
          "photos load through an authorized fetch, not a public URL",
          "image src is not a blob: URL, so it was fetched without the token")
    check(seen.get("caption") == "look what I did", "the caption arrives with it")


def check_side_quests_still_work(b: Browser, base: str, app) -> None:
    """Side Quests is the solo habit tracker, and it had no coverage at all.

    A blanket CSS-class rename changed the form's input id and creation broke
    silently — the handler is async, so the TypeError surfaced as an unhandled
    rejection rather than an error anyone would see. 295 tests stayed green.
    """
    print("\nSide Quests — the solo habit tracker")
    _, token = make_user(app, "sidequest")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    if not check(go_to_pane(b, "progress"), "Side Quests is reachable from the nav"):
        return
    check(bool(b.js("(()=>{const i=document.getElementById('challenge-title');"
                    " return !!i && !!i.offsetParent;})()")),
          "the side-quest form is visible once you are there")

    b.js("(()=>{const i=document.getElementById('challenge-title');"
         " i.value='Read for 10 minutes'; return 1;})()")
    b.js("(()=>{const f=document.getElementById('create-form');"
         " f.dispatchEvent(new Event('submit',{cancelable:true})); return 1;})()")
    time.sleep(2.5)

    listed = b.js("document.getElementById('challenges-list').innerText") or ""
    check("Read for 10 minutes" in listed,
          "adding a side quest actually adds it",
          f"list shows {listed[:80]!r}")
    check("Check In" in listed, "a new side quest offers a check-in")


def check_step_up_is_offered_not_imposed(b: Browser, base: str, app) -> None:
    """The progression affordance, driven through the real UI.

    The thing being verified is not that the number can go up — a unit test
    covers that. It is that the prescription a practised user sees is still the
    ORIGINAL one until they choose otherwise, because a number that rises on
    its own turns a daily habit into a target with a failure condition.
    """
    print("\nProgression — 'Want a little more?' is an offer")
    from app import DailyCompletion, User, db

    username, token = make_user(app, "stepup")
    daily = _api(base, "/api/daily", token=token)
    keys = [e["key"] for e in daily["exercises"]]
    before = {e["key"]: e["reps_or_duration"] for e in daily["exercises"]}

    # Enough practice of the same movements to earn the offer. Spaced out
    # deliberately: a run of seven consecutive days plus a gap makes this a
    # returning user, and the Rise Again ceremony then takes over the screen —
    # correctly, but it is not what this check is looking at.
    with app.app_context():
        row = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
        for off in range(4, 20, 2):
            when = dt.date.today() - dt.timedelta(days=off)
            for key in keys:
                db.session.add(DailyCompletion(user_id=row.id, date=when, exercise_key=key))
        db.session.commit()

    after = _api(base, "/api/daily", token=token)
    same = [e for e in after["exercises"] if e["key"] in before]
    check(bool(same) and all(e["reps_or_duration"] == before[e["key"]] for e in same),
          "practice never raises the prescription underneath the user",
          str([(e["key"], before.get(e["key"]), e["reps_or_duration"]) for e in same][:2]))
    offered = [e for e in after["exercises"] if e.get("step_up")]
    if not check(bool(offered), "a practised movement offers a larger version"):
        return

    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    count = b.js("document.querySelectorAll('.daily-exercise-stepup').length")
    if not check(bool(count), "the offer is visible in the mission", f"found {count}"):
        return
    label = b.js("document.querySelector('.daily-exercise-stepup').textContent") or ""
    check("Want a little more?" in label, "it reads as an offer", label[:80])

    # Taking it swaps the line in place and the offer goes away.
    b.js("document.querySelector('.daily-exercise-stepup').click()")
    time.sleep(0.4)
    remaining = b.js("document.querySelectorAll('.daily-exercise-stepup').length")
    check(remaining == count - 1, "taking it removes the offer", f"{count} -> {remaining}")
    check(not b.js("(()=>{const r=document.querySelector('.daily-exercise-row');"
                   " return r && /Want a little more/.test(r.textContent);})()"),
          "the row now shows the larger prescription instead of the offer")


def check_panes_and_solo_first(b: Browser, base: str, app) -> None:
    """The home screen is three panes, and Team is not one of them by default.

    The load-bearing assertion here is the last one: a person who has never
    joined a team must not be shown a permanent tab labelled Team. A standing
    tab is a daily nudge toward a feature someone has chosen not to use, and
    the solo experience is meant to be whole rather than a version of the app
    with a gap in it.
    """
    print("\nPanes — and a solo user is not nudged toward Teams")
    # Needs a completed mission behind them: the reminder prompt is withheld
    # until someone has finished one, and that prompt is exactly the element
    # that leaked onto Today when the pane rule lost a specificity fight with
    # an id selector. A fresh account never renders it, so a fresh account
    # cannot catch the bug.
    _, token = make_user(app, "panes", seed_days=[2, 1])
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    check(b.js("document.querySelector('main.container').dataset.pane") == "today",
          "the app opens on Today")
    check(bool(b.js("(()=>{const m=document.querySelector('.daily-card');"
                    " return m && !!m.offsetParent;})()")),
          "the mission is what you see first")
    check(not b.js("(()=>{const j=document.getElementById('side-quests-section');"
                   " return !!j && !!j.offsetParent;})()"),
          "Side Quests is not competing with the mission on Today")

    solo_tab = b.js("!document.getElementById('pane-nav-team').hidden")
    check(not solo_tab, "a solo user gets no Team tab", f"tab visible: {solo_tab}")

    # Every member of an inactive pane must really be gone, not merely marked.
    # An id-based `display` rule outranks the pane selector, which is how the
    # reminder prompt stayed on Today with its class correctly applied.
    leaked = b.js(
        "(()=>{const m=document.querySelector('main.container');const out=[];"
        " for (const p of ['progress','team']) {"
        "   for (const el of m.querySelectorAll('.pane-'+p)) {"
        "     if (el.offsetParent) out.push((el.id||el.className)+' ['+p+']'); } }"
        " return out.join(', ');})()"
    ) or ""
    check(not leaked, "nothing from another pane leaks onto Today", leaked)

    check(go_to_pane(b, "progress"), "Progress is reachable")
    check(bool(b.js("(()=>{const j=document.getElementById('journey-card');"
                    " return j && !!j.offsetParent;})()")),
          "the Journey card lives in Progress")
    check(bool(b.js("(()=>{const i=document.getElementById('solo-team-invite');"
                    " return i && !!i.offsetParent;})()")),
          "the one quiet way in to Teams is at the foot of Progress")

    # Asking for it is what reveals the tab.
    b.js("document.getElementById('solo-team-invite-btn').click()")
    time.sleep(0.6)
    check(b.js("document.querySelector('main.container').dataset.pane") == "team"
          and not b.js("document.getElementById('pane-nav-team').hidden"),
          "asking to add people opens Teams and keeps the tab")

    # Rickie must be openable from Today, where his buttons are.
    #
    # Scope note, because it would be easy to over-claim this one: the coach
    # panel's old mount point inserted it as a SIBLING of the Side Quests
    # section, so moving that section into another pane did not in fact break
    # it, and this check passes with either version. It was still worth making
    # the mount explicit — a sibling relationship with a section that has since
    # moved panes is a trap for whoever wraps the panes in container elements
    # next — but what this assertion actually proves is only that Rickie opens
    # and is visible from Today.
    go_to_pane(b, "today")
    b.js("(()=>{const x=[...document.querySelectorAll('button')]"
         ".filter(e=>e.offsetParent && e.id!=='rickie-roam-toggle')"
         ".find(e=>/Rickie|Ask|Coach/i.test(e.textContent)); if(x) x.click(); return 1;})()")
    time.sleep(1.2)
    check(bool(b.js("(()=>{const p=document.querySelector('.coach-panel');"
                    " return p && !p.hidden && !!p.offsetParent;})()")),
          "Rickie opens from Today and is actually visible")


def check_someone_can_actually_sign_up(b: Browser, base: str, app) -> None:
    """The first sixty seconds, through the real form.

    Every other check in this file mints an account directly and drops a token
    into localStorage, because registration is rate limited and a harness that
    signs up eleven times fails on the limiter instead of on anything real.
    The cost of that shortcut is that the single most important path in the
    product — a person typing a username and arriving at their first mission —
    had no browser coverage whatsoever. This is the one check that pays the
    rate-limit cost and walks in the front door.
    """
    print("\nSigning up — the front door")
    b.goto(base + "/", wait=1.5)
    b.reset_storage()
    b.goto(base + "/", wait=2.0)

    check(bool(b.js("(()=>{const f=document.getElementById('register-form');"
                    " return f && !!f.offsetParent;})()")),
          "the sign-up form is what a new visitor sees")

    username = f"uicheck_signup_{int(time.time() * 1000) % 1000000}"
    b.js("(function(){document.getElementById('reg-username').value="
         + json.dumps(username)
         + "; document.getElementById('reg-password').value='Passw0rd!x'; return 1;})()")
    b.js("(()=>{const f=document.getElementById('register-form');"
         " f.dispatchEvent(new Event('submit',{cancelable:true})); return 1;})()")
    time.sleep(4.0)

    err = (b.js("document.getElementById('register-error').textContent") or "").strip()
    if not check(not err, "signing up reports no error", err):
        return
    if not check(bool(b.js("(()=>{const d=document.getElementById('dashboard-view');"
                           " return d && !d.hidden;})()")),
                 "it lands on the dashboard rather than staying on the form"):
        return
    rows = b.js("document.querySelectorAll('.daily-exercise-row').length")
    check(rows == 5, "a brand-new account has a five-exercise mission waiting",
          f"found {rows} rows")
    check(bool(b.js("(()=>{const btn=[...document.querySelectorAll('button')]"
                    ".find(e=>e.textContent.trim()==='I did this'); return !!btn;})()")),
          "and something to tap")


def check_brain_boost_can_be_answered(b: Browser, base: str, app) -> None:
    """Tapping an answer, in a browser.

    The content library has plenty of tests and none of them press a button.
    193 questions were rewritten in one pass — options replaced, several
    questions reworded, 18 dropped and 21 added — and the thing that would
    catch a mistake in the wiring is a tap, not an assertion about a dict.
    """
    print("\nBrain Boost — can you answer it?")
    _, token = make_user(app, "boost")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    # Brain Boost is the reward for finishing, so the mission has to be done
    # before it renders at all. Completed through the API rather than by
    # tapping five buttons — the tapping has its own check, and this one is
    # about the question.
    daily = _api(base, "/api/daily", token=token)
    for ex in daily["exercises"]:
        _api(base, f"/api/daily/{ex['key']}/complete", "POST", token)
    b.goto(base + "/", wait=3.5)

    b.js("(()=>{const r=[...document.querySelectorAll('button')]"
         ".find(e=>/Reveal Brain Boost/i.test(e.textContent)); if(r) r.click(); return 1;})()")
    time.sleep(1.2)

    count = b.js("document.querySelectorAll('.bb-option-btn').length")
    if not check(count == 4, "four options are on screen", f"found {count}"):
        return

    question = b.js("(document.querySelector('.bb-question-text')||{}).textContent") or ""
    check(question.strip().endswith("?"), "it is a question", question[:70])

    b.js("document.querySelectorAll('.bb-option-btn')[0].click()")
    time.sleep(2.0)

    feedback = (b.js("(document.querySelector('.bb-feedback')||{}).textContent") or "").strip()
    check(bool(feedback), "answering says whether you got it", feedback[:60])
    explanation = (b.js("(document.querySelector('.bb-explanation')||{}).textContent") or "").strip()
    check(len(explanation) > 40,
          "and explains it either way — a wrong answer is where the fact lands",
          explanation[:70])
    # Never-negative: getting it wrong must not be scolded.
    low = (feedback + " " + explanation).lower()
    for word in ("wrong again", "you failed", "incorrect!", "nope!"):
        check(word not in low, f"the wrong-answer copy stays kind ({word!r} absent)")


def check_coming_back_after_a_while(b: Browser, base: str, app) -> None:
    """The returning user, in a browser.

    Before this, the selection function was never told how long anybody had
    been away, so someone who had been training at advanced and had not moved
    for forty days was handed their exact peak load on the morning they came
    back. The fix is not "an easier day as a reward for missing" — an absence
    is not a failure and nothing is taken away. It is that what a body could do
    in March is not a claim about this morning.
    """
    print("\nComing back after a while")
    from app import DailyCompletion, User, db

    username, token = make_user(app, "returning")
    with app.app_context():
        row = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
        row.skill_level = "advanced"
        row.xp_total, row.acorns_total = 2400, 150
        last = dt.date.today() - dt.timedelta(days=41)
        for d in range(30):
            for key in ("archer_push_up", "pistol_squat_progression", "hollow_body_hold",
                        "deep_squat_hold", "burpee"):
                db.session.add(DailyCompletion(user_id=row.id, date=last - dt.timedelta(days=d),
                                               exercise_key=key))
        db.session.commit()

    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.5)

    daily = _api(base, "/api/daily", token=token)
    check(daily["effort"]["level"] == "easy",
          "a long absence pre-selects a gentler day", daily["effort"]["level"])
    check(daily["effort"]["chosen"] is False,
          "it is suggested, not decided for them")
    explosive = [e["name"] for e in daily["exercises"] if e.get("from_easier_tier")]
    check(all(not e.get("from_next_tier") for e in daily["exercises"]),
          "no movement from a harder level on the first day back", str(explosive))

    note = (b.js("(document.getElementById('daily-effort-note')||{}).textContent") or "")
    check("earned" in note.lower(),
          "and it says nothing they earned has moved", note[:70])
    for scolding in ("been a while", "welcome back", "missed", "40"):
        check(scolding not in note.lower(),
              f"the line does not mention the absence ({scolding!r})")

    me = _api(base, "/api/me", token=token)
    check(me.get("xp_total") == 2400 and me.get("total_missions") == 30
          and me.get("best_streak") == 30,
          "nothing earned was lost by being away",
          f"xp={me.get('xp_total')} missions={me.get('total_missions')} best={me.get('best_streak')}")

    labels = b.js("(()=>[...document.querySelectorAll('.effort-btn')]"
                  ".map(e=>e.textContent.trim()+(e.classList.contains('is-on')?'*':'')))()") or []
    check(len(labels) == 3 and any(x.endswith("*") for x in labels),
          "the choice is on screen with one option selected", str(labels))

    # And they can overrule it in one tap.
    b.js("(()=>{const b=[...document.querySelectorAll('.effort-btn')]"
         ".find(e=>/My usual/.test(e.textContent)); if(b) b.click(); return 1;})()")
    time.sleep(2.5)
    after = _api(base, "/api/daily", token=token)
    check(after["effort"]["level"] == "usual" and after["effort"]["chosen"] is True,
          "one tap overrules the suggestion", after["effort"]["level"])


def check_discovery_types_reach_a_reader(b: Browser, base: str, app) -> None:
    """Riddles, mini-experiments and Rickie's asides, on screen.

    The content store gained three types that no surface served. Content that
    exists and cannot be reached is the failure this project already has a rule
    about — the R2 team layer shipped six features nobody could get to — so this
    drives each new type through the real card rather than trusting the count.
    """
    print("\nDiscovery — the new content types actually render")
    from app import INSIGHT_LIBRARY

    wanted = ("riddle", "experiment", "rickie")
    have = {t for t in wanted if any(i.get("type") == t for i in INSIGHT_LIBRARY)}
    if not check(have == set(wanted), "all three new types are in the served library",
                 f"found {sorted(have)}"):
        return

    _, token = make_user(app, "discovery")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    for kind in wanted:
        item = next(i for i in INSIGHT_LIBRARY if i.get("type") == kind)
        # Render the real card with a real item of this type.
        b.js("(()=>{const c=renderInsightCard(" + json.dumps(item) + ");"
             " c.id='uicheck-discovery'; document.body.appendChild(c);"
             " const r=c.querySelector('.insight-reveal-btn'); if(r) r.click();"
             " return 1;})()")
        time.sleep(0.4)
        label = b.js("(document.querySelector('#uicheck-discovery .insight-category')"
                     "||{}).textContent") or ""
        shown = b.js("(document.querySelector('#uicheck-discovery .insight-text')"
                     "||{}).textContent") or ""
        check(bool(label) and bool(shown.strip()),
              f"a {kind} renders with a label and a body", f"{label!r} / {shown[:40]!r}")

        if kind == "riddle":
            answer_hidden = b.js(
                "(()=>{const a=document.querySelector('#uicheck-discovery "
                ".insight-riddle-answer'); return !!a && a.hidden;})()")
            check(answer_hidden, "a riddle does not give away its own answer")
            b.js("(()=>{const btns=[...document.querySelectorAll('#uicheck-discovery button')]"
                 ".filter(x=>/Give up/.test(x.textContent)); if(btns[0]) btns[0].click();"
                 " return 1;})()")
            time.sleep(0.3)
            revealed = b.js(
                "(()=>{const a=document.querySelector('#uicheck-discovery "
                ".insight-riddle-answer'); return !!a && !a.hidden && !!a.textContent.trim();})()")
            check(revealed, "and gives it up when asked")

        b.js("(()=>{const c=document.getElementById('uicheck-discovery');"
             " if(c) c.remove(); return 1;})()")


def check_display_name_can_be_set_changed_and_cleared(b: Browser, base: str, app) -> None:
    """The control that decides what Rickie calls somebody out loud.

    This existed as a column, a validator and an endpoint with nineteen tests,
    and no way for a person to reach any of it. The tests proved the rule; they
    could not prove a user could apply it.

    Clearing is the case worth driving in a browser rather than asserting in
    pytest: an empty box has to mean "use no name", not "no change submitted",
    and that distinction lives entirely in the front end.
    """
    print("\nThe name Rickie calls you")
    username, token = make_user(app, "dname")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=2.5)

    b.js("(()=>{const m=document.getElementById('settings-menu');"
         " if(m) m.hidden=false; return 1;})()")
    time.sleep(0.4)

    row_visible = b.js("(()=>{const r=document.getElementById('settings-row-name');"
                       " return !!(r && !r.hidden);})()")
    if not check(bool(row_visible), "a registered user can find the control"):
        return

    # The username here is uicheck_dname_<digits> — a machine handle with a long
    # digit run, so the safe-name rule should refuse to fall back to it.
    helped = (b.js("document.getElementById('display-name-help').textContent") or "").strip()
    check("isn't using a name" in helped,
          "with no name set, it says he is not using one",
          helped)

    def set_name(value):
        b.js("(()=>{const i=document.getElementById('display-name-input');"
             f" i.value={json.dumps(value)};"
             " i.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()")
        time.sleep(1.6)

    # SET
    set_name("Olivia")
    check(b.js("document.getElementById('display-name-input').value") == "Olivia",
          "a name can be set")
    helped = (b.js("document.getElementById('display-name-help').textContent") or "")
    check('"Olivia"' in helped, "and the page says what he now calls them", helped.strip())

    # CHANGE
    set_name("Liv")
    helped = (b.js("document.getElementById('display-name-help').textContent") or "")
    check('"Liv"' in helped, "it can be changed", helped.strip())

    # REJECTED — an email address is the case this control exists for
    set_name("olivia@example.com")
    helped = (b.js("document.getElementById('display-name-help').textContent") or "")
    check("email" in helped.lower(), "an email address is refused, and says why",
          helped.strip())
    check(b.js("document.getElementById('display-name-input').value") == "Liv",
          "and the box goes back to the stored name rather than keeping it")

    # CLEAR
    set_name("")
    check(b.js("document.getElementById('display-name-input').value") == "",
          "it can be cleared")
    helped = (b.js("document.getElementById('display-name-help').textContent") or "").strip()
    check("isn't using a name" in helped,
          "and clearing really means no name, not a silent fallback to the login",
          helped)

    # It must survive a reload — otherwise it only ever lived in the DOM.
    set_name("Olivia")
    b.goto(base + "/", wait=2.5)
    b.js("(()=>{const m=document.getElementById('settings-menu');"
         " if(m) m.hidden=false; return 1;})()")
    time.sleep(0.4)
    check(b.js("document.getElementById('display-name-input').value") == "Olivia",
          "and it survives a reload")

    # The placeholder must actually fit. It read "Leave blank fo" on a phone,
    # which looks like a broken field rather than a hint.
    fits = b.js("(()=>{const i=document.getElementById('display-name-input');"
                " if(!i) return false; const c=document.createElement('canvas')"
                ".getContext('2d'); c.font=getComputedStyle(i).font;"
                " return c.measureText(i.placeholder).width <= i.clientWidth - 12;})()")
    check(bool(fits), "its placeholder fits the box at phone width")

    # And the name reaches the app, not just the chat prompt it was built for.
    set_name("Olivia")
    b.goto(base + "/", wait=2.5)
    reaches = b.js("(()=>{return typeof _withName==='function' && "
                   "/Olivia/.test(_withName('Morning.'));})()")
    check(bool(reaches), "the name is actually used by the app's own greeting")

    # A guest has no account to store it on.
    b.reset_storage()
    b.goto(base + "/", wait=1.5)
    b.goto(base + "/", wait=2.0)
    b.js("handleGuestMode()")          # the app's own entry point, as elsewhere here
    time.sleep(2.5)
    in_guest = b.js("(()=>{const g=document.getElementById('guest-mode-banner');"
                    " return !!(g && !g.hidden);})()")
    if not check(bool(in_guest), "the harness really is in guest mode"):
        return
    b.js("(()=>{const m=document.getElementById('settings-menu');"
         " if(m) m.hidden=false; return 1;})()")
    time.sleep(0.4)
    hidden = b.js("(()=>{const r=document.getElementById('settings-row-name');"
                  " return !r || r.hidden;})()")
    check(bool(hidden), "and a guest is not offered a control they cannot use")
    help_hidden = b.js("(()=>{const h=document.getElementById('display-name-help');"
                       " return !h || h.hidden;})()")
    check(bool(help_hidden), "nor left a stray sentence about a missing control")


def check_rickie_roams(b: Browser, base: str, app) -> None:
    """Rickie, moving, and never in the way.

    The obstruction rule is checked against the real page rather than against
    the engine's model of it: he is placed where the engine says is clear, and
    then every visible control and every piece of exercise text is measured to
    confirm none of them is under him. A character that dodges according to its
    own map is not the same as a character that dodges.
    """
    print("\nRickie — roaming")
    _, token = make_user(app, "roam")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=3.0)

    if not check(bool(b.js("!!window.RickieRoam && !!document.getElementById('rickie-roam-band')")),
                 "he is on the dashboard"):
        return
    check(b.js("getComputedStyle(document.querySelector('.rickie-roam-band')).position") == "fixed",
          "his layer is fixed, so he can never shift the layout")
    check(b.js("getComputedStyle(document.querySelector('.rickie-roam-band')).pointerEvents") == "none",
          "and never swallows a tap")

    # Variety: a weighted pick with the last three excluded.
    #
    # 600 draws, not 60. At 60 this block was statistically flaky and failed on
    # luck rather than on Rickie: sit+doze carry ~28% of the weight, so
    # `resting >= 12` sat about 1.6 standard deviations out and failed roughly
    # one run in sixteen, and the two rare behaviours carry ~2% between them, so
    # "the rare behaviours are reachable" drew a blank in about a third of runs.
    # A suite that cries wolf every third run is a suite people stop reading.
    #
    # The loop is pure arithmetic over the weighted picker — no rendering, no
    # timers — so ten times the sample costs nothing and puts every threshold
    # several standard deviations from its bound.
    DRAWS = 600
    ids = (b.js("""(()=>{const out=[];const st=RickieRoam._state;const keep=st.recent.slice();
      for(let i=0;i<%d;i++){const bh=RickieRoam._pick(RickieRoam._behaviours, st.recent);
        out.push(bh.id); st.recent.push(bh.id); while(st.recent.length>3) st.recent.shift();}
      st.recent=keep; return out.join(',');})()""" % DRAWS) or "").split(",")
    distinct = len(set(ids))
    longest, run = 1, 1
    for i in range(1, len(ids)):
        run = run + 1 if ids[i] == ids[i - 1] else 1
        longest = max(longest, run)
    check(distinct >= 9,
          f"he has at least nine things he might do ({distinct} in {DRAWS} draws)")
    check(longest == 1, f"and never does the same one twice running (longest run {longest})")
    rare = sum(1 for i in ids if i in ("tumble", "acornjuggle"))
    check(rare > 0, f"the rare behaviours are reachable ({rare} in {DRAWS})")
    resting = sum(1 for i in ids if i in ("sit", "doze"))
    # ~28% expected; 20% is about four standard deviations below it at this n,
    # and still far enough above zero to catch a mascot that never sits down.
    check(resting >= DRAWS * 0.20,
          f"and he rests a good deal of the time ({resting}/{DRAWS}, "
          f"{resting / DRAWS:.0%})")

    # Obstruction, measured on the page.
    spots = b.js("RickieRoam._freeSpots().length")
    if not check(spots > 0, "there is somewhere clear for him to stand", f"{spots} free"):
        return
    blocked = []
    b.js("RickieRoam.setPaused(true)")   # he must hold still to be measured
    for _ in range(8):
        b.js("""(()=>{const s=RickieRoam._somewhereClear(); if(!s) return 0;
          const e=document.querySelector('.rickie-roam');
          const st=e.parentNode.getBoundingClientRect();
          e.style.transform='translate('+Math.round(s.x*(st.width-56))+'px,'+
            Math.round(s.y*(st.height-56))+'px)'; return 1;})()""")
        # Measures EVERY VISIBLE PIECE OF TEXT, not a list of class names.
        #
        # This check passed for weeks while an independent review of the running
        # app found him parked over the mission counter, the date subtitle, a
        # button label and the Brain Boost question in four of six phone
        # screenshots. It passed because it asked about the same hand-written
        # list the placement engine used: both knew `.daily-exercise-name`,
        # neither knew about the counter. A test that shares its subject's blind
        # spot is not a test, it is a second opinion from the same person.
        hit = b.js("""(()=>{const r=document.querySelector('.rickie-roam').getBoundingClientRect();
          const band=document.getElementById('rickie-roam-band');
          const bad=[];
          const textBearing=(n)=>{for(const c of n.childNodes)
            if(c.nodeType===3&&c.nodeValue&&c.nodeValue.trim()) return true; return false;};
          const nodes=new Set(document.querySelectorAll(
              'button,a,input,select,textarea,img,svg,.bb-option-btn'));
          for(const el of document.body.querySelectorAll('*'))
            if(textBearing(el)) nodes.add(el);
          for(const el of nodes){
            if(!el.offsetParent) continue;
            if(band&&(el===band||band.contains(el))) continue;
            const q=el.getBoundingClientRect();
            if(!q.width||!q.height) continue;
            if(!(q.right<r.left||q.left>r.right||q.bottom<r.top||q.top>r.bottom))
              bad.push((el.id||el.className||el.tagName).toString().slice(0,30));
          } return bad.join('|');})()""")
        if hit:
            blocked.append(hit)
    b.js("RickieRoam.setPaused(false)")
    check(not blocked, "every position he may stand in is clear of controls AND text",
          "; ".join(blocked[:3]))

    # A tap at his position reaches the page underneath.
    #
    # Asks the DOM whether the hit element is INSIDE the roaming layer, rather
    # than whether its class name happens to contain "rickie". The substring
    # form failed intermittently on `today-rickie` — the static Rickie
    # illustration on the Today card, which is page content and exactly what a
    # tap passing through SHOULD land on. Where he stands is random, so that
    # read as a tap-blocking bug roughly whenever he wandered over that card.
    under = b.js("""(()=>{const el=document.querySelector('.rickie-roam');
      const q=el.getBoundingClientRect();
      const hit=document.elementFromPoint(q.left+q.width/2, q.top+q.height/2);
      if(!hit) return 'nothing';
      const band=document.getElementById('rickie-roam-band');
      const swallowed=(band&&band.contains(hit))||el.contains(hit)||hit===el||hit===band;
      return (swallowed?'SWALLOWED:':'through:')+
             (hit.id||hit.className||hit.tagName).toString().slice(0,40);})()""")
    check(not str(under).startswith("SWALLOWED"),
          "a tap where he stands reaches the page, not him", str(under))

    # He stops while somebody is typing.
    b.js("""(()=>{const i=document.createElement('input'); i.id='roamcheck-input';
      i.style.cssText='position:fixed;top:8px;left:8px;z-index:99';
      document.body.appendChild(i); i.focus(); return 1;})()""")
    time.sleep(1.3)
    check(bool(b.js("RickieRoam._state.suspended")), "he settles while a field is focused")
    b.js("(()=>{const i=document.getElementById('roamcheck-input'); i.blur(); i.remove(); return 1;})()")
    time.sleep(1.3)
    check(not b.js("RickieRoam._state.suspended"), "and carries on afterwards")

    # The pause control, which must be reachable without a mouse.
    b.js("RickieRoam.setPaused(true)")
    time.sleep(0.3)
    check(bool(b.js("RickieRoam.isPaused()")), "roaming can be paused")
    label = (b.js("(document.getElementById('rickie-roam-toggle')||{}).textContent") or "").strip()
    check("roam" in label.lower(), "and the control says what it will do", label)
    check(b.js("(document.getElementById('rickie-roam-toggle')||{}).getAttribute('aria-pressed')") == "true",
          "with its state exposed to assistive tech")
    b.js("RickieRoam.setPaused(false)")

    # Reactions vary rather than replaying one animation.
    poses = (b.js("""(()=>{const seen=[];for(let i=0;i<24;i++){
      RickieRoam._state.busy=false; RickieRoam.react('mission_done');
      seen.push(RickieRoam._state.pose);} return seen.join(',');})()""") or "").split(",")
    check(len(set(poses)) >= 2, f"a celebration is not always the same one ({sorted(set(poses))})")


def check_page_is_clean(b: Browser, base: str, app) -> None:
    print("\nThe page itself, at phone width")
    _, token = make_user(app, "clean")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.js("window.__errs=[]; window.addEventListener('error',e=>window.__errs.push(String(e.message)));"
         "window.addEventListener('unhandledrejection',e=>window.__errs.push('rejection: '+e.reason));")
    b.goto(base + "/", wait=3.5)

    errs = json.loads(b.js("JSON.stringify(window.__errs||[])") or "[]")
    check(not errs, "no uncaught page errors", f"{errs[:3]}")

    broken = b.js("[...document.images].filter(i=>i.complete && i.naturalWidth===0).length")
    check(broken == 0, "no broken images", f"{broken} broken")

    thumbs = b.js("document.querySelectorAll('.daily-exercise-thumb').length")
    check(thumbs == 5, "every exercise in the mission shows its illustration",
          f"found {thumbs}")

    overflow = b.js("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    check(not overflow, "no horizontal scrolling at 390px")

    small = b.js(
        "JSON.stringify([...document.querySelectorAll('button,select,a[href]')]"
        ".filter(e=>{const r=e.getBoundingClientRect();"
        "return r.width>0 && r.height>0 && (r.height<44||r.width<44);})"
        ".map(e=>(e.textContent||e.className||e.tagName).trim().slice(0,28)).slice(0,8))"
    )
    small_list = json.loads(small or "[]")
    check(not small_list, "every visible control meets the 44px tap target",
          f"too small: {small_list}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", nargs="?", default=None)
    parser.add_argument("--base-url", dest="base_url_flag", default=None)
    args = parser.parse_args()
    base = (args.base_url or args.base_url_flag
            or os.environ.get("UICHECK_BASE_URL") or "http://localhost:5000").rstrip("/")

    host = urlparse(base).hostname or ""
    if host not in LOCAL_HOSTS:
        print(f"refusing to run against {base!r}: this check writes directly to the "
              "database and creates accounts, so it is local-only. Use "
              "scripts/verify_all.py for anything remote.")
        return 2

    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            if r.status != 200:
                raise RuntimeError(f"/health returned {r.status}")
    except Exception as exc:
        print(f"server not reachable at {base} ({exc}). Start it with `make run`.")
        return 2

    sys.path.insert(0, str(ROOT))
    # Load the same .env the running server booted with. Tokens are minted
    # in-process here, so a different JWT_SECRET_KEY would produce tokens the
    # server rejects — and every check would fail for the wrong reason.
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    os.environ.setdefault("SECRET_KEY", "uicheck")
    os.environ.setdefault("JWT_SECRET_KEY", "uicheck")
    try:
        from app import app as flask_app
    except Exception as exc:
        print(f"could not import app.py ({exc}).")
        return 2

    print(f"StreakFit UI check — {base} at 390x844\n" + "=" * 58)
    try:
        browser = Browser()
    except EnvironmentError as exc:
        print(f"cannot start a browser ({exc}). Nothing was verified.")
        return 2

    try:
        browser.goto(base + "/", wait=1.5)
        browser.reset_storage()
        check_returning_user_is_acknowledged(browser, base, flask_app)
        check_first_mission_celebration(browser, base, flask_app)
        check_guest_gets_the_celebration(browser, base)
        check_team_witness(browser, base, flask_app)
        check_photo_sharing(browser, base, flask_app)
        check_side_quests_still_work(browser, base, flask_app)
        check_step_up_is_offered_not_imposed(browser, base, flask_app)
        check_panes_and_solo_first(browser, base, flask_app)
        check_brain_boost_can_be_answered(browser, base, flask_app)
        check_someone_can_actually_sign_up(browser, base, flask_app)
        check_coming_back_after_a_while(browser, base, flask_app)
        check_discovery_types_reach_a_reader(browser, base, flask_app)
        check_display_name_can_be_set_changed_and_cleared(browser, base, flask_app)
        check_rickie_roams(browser, base, flask_app)
        check_page_is_clean(browser, base, flask_app)
    except Exception as exc:  # a crash must never read as a pass
        bad(f"check run crashed: {type(exc).__name__}: {exc}")
    finally:
        browser.close()

    print("\n" + "=" * 58)
    if failures:
        print(f"UI CHECK FAILED — {len(failures)} of {len(passes) + len(failures)} checks:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"UI CHECK PASSED — {len(passes)} checks, 0 problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
