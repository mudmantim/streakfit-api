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


def seed_today_keys(app, username: str, base: str, token: str):
    """Mark today's actual mission keys as done on a previous day, so the user
    is a genuine returning user for whom nothing today is new."""
    from app import DailyCompletion, User, db

    daily = _api(base, "/api/daily", token=token)
    keys = [e["key"] for e in daily["exercises"]]
    with app.app_context():
        row = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
        when = dt.date.today() - dt.timedelta(days=1)
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
