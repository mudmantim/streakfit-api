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
import re
import socket
import struct
import subprocess
import sys
import tempfile
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

    def __init__(self, width: int = 390, height: int = 844, port: int | None = None):
        """port=None picks a free one. Pass an explicit port only to attach.

        This used to default to 9333 with a shared profile directory. Two
        people running this file at the same time — or one person and one
        agent — silently attached to EACH OTHER'S Chrome, because the second
        launch found the port busy and the CDP client happily connected to the
        browser already there. The symptom is not an error: it is checks that
        fail on state somebody else's run created, differently every time.
        That cost an independent reviewer several runs and cost several of
        mine on the same afternoon before either of us worked out why.

        A free port and a profile directory named after it make concurrent
        runs independent. STREAKFIT_UICHECK_PORT still pins it for anyone who
        needs to attach a debugger.
        """
        if port is None:
            env_port = os.environ.get("STREAKFIT_UICHECK_PORT", "").strip()
            if env_port.isdigit():
                port = int(env_port)
            else:
                with socket.socket() as s:
                    s.bind(("127.0.0.1", 0))
                    port = s.getsockname()[1]
        self.port = port
        for binary in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            if _which(binary):
                break
        else:
            raise EnvironmentError("no Chrome/Chromium binary found on PATH")
        self.proc = subprocess.Popen(
            [
                binary, "--headless=new", f"--remote-debugging-port={port}",
                f"--user-data-dir={tempfile.gettempdir()}/streakfit-uicheck-{port}",
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


def touch_tap(b: Browser, target_js: str) -> dict:
    """Tap an element the way a thumb does: a touch at its on-screen centre.

    Everything else in this file taps with `el.click()`, which goes straight to
    the element and so can never notice something drawn on top of it. This
    dispatches a real touch at coordinates, so whatever is actually under the
    finger receives it, and reports what that was. `target_js` is an expression
    that evaluates to the element.
    """
    measure = (f"(()=>{{const el=({target_js}); if(!el) return null;"
               "const r=el.getBoundingClientRect(); const x=r.left+r.width/2, y=r.top+r.height/2;"
               "const top=document.elementFromPoint(x,y);"
               "return JSON.stringify({x, y, onTarget: !!top && (top===el || el.contains(top)),"
               " hit: top ? (top.className || top.tagName) : null});})()")
    b.js(f"(()=>{{const el=({target_js}); if(el) el.scrollIntoView({{block:'center'}}); return 1;}})()")
    time.sleep(0.4)
    spot = b.js(measure)
    if not spot:
        return {"found": False}
    spot = json.loads(spot)
    point = [{"x": spot["x"], "y": spot["y"], "radiusX": 6, "radiusY": 6, "force": 1, "id": 1}]
    b.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=point)
    time.sleep(0.08)
    b.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
    return dict(spot, found=True)


def check_a_completion_that_did_not_save_says_so(b: Browser, base: str, app) -> None:
    """A real-phone test: "I did this" appeared to respond, nothing stuck, the
    mission sat at 0/5, and nothing on screen said why. The server log showed
    the completion requests never arrived; the app then put the button back
    exactly as it was, silently. Failure has to be visible, retryable, and
    never counted — and success afterwards has to be real and survive a reload.
    """
    print("\nA completion that did not save says so, and can be retried")
    username, token = make_user(app, "complete_fail")
    b.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
    try:
        b.goto(base + "/", wait=1.0)
        b.reset_storage()
        b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
        b.goto(base + "/", wait=3.5)

        # The row that is NOT next, so its button starts outlined: that is the
        # one where a stuck hover used to fill it solid purple.
        row = "document.querySelectorAll('.daily-exercise-row')[1]"
        btn = f"{row}.querySelector('.btn-daily-complete')"
        name = b.js(f"(()=>{{const n={row}.querySelector('.daily-exercise-name');"
                    " return n ? n.textContent.trim() : null;})()")

        # Every completion request fails, first the way a server error does,
        # then the way an unreachable server does (the phone's case).
        b.js("""(()=>{ const real = window.fetch; window.__completeMode = 'http503';
            window.fetch = function (u, o) {
              if (String(u).includes('/complete') && window.__completeMode === 'http503')
                return Promise.resolve(new Response('{"error":"unavailable"}',
                  {status: 503, headers: {'Content-Type': 'application/json'}}));
              if (String(u).includes('/complete') && window.__completeMode === 'offline')
                return Promise.reject(new TypeError('Failed to fetch'));
              return real.apply(this, arguments);
            }; return 1; })()""")

        spot = touch_tap(b, btn)
        check(spot.get("found") and spot.get("onTarget"),
              "a touch on 'I did this' lands on the button, not on something drawn over it",
              f"the touch landed on {spot.get('hit')!r}")
        time.sleep(1.2)
        err = b.js(f"(()=>{{const e={row}.querySelector('.daily-complete-error');"
                   " return e ? JSON.stringify([e.textContent, e.getAttribute('role'), !!e.offsetParent]) : null;})()")
        err = json.loads(err) if err else None
        check(bool(err) and err[2] and "Not saved" in err[0],
              "a completion the server refused puts 'Not saved' on screen beside the button",
              f"saw {err!r}")
        check(bool(err) and err[1] == "alert",
              "that message is announced to a screen reader (role=alert)",
              f"role was {err and err[1]!r}")

        b.js("window.__completeMode = 'offline'")
        touch_tap(b, btn)
        time.sleep(1.2)
        errs = json.loads(b.js("JSON.stringify([...document.querySelectorAll('.daily-complete-error')]"
                               ".map(e => e.textContent))"))
        check(len(errs) == 1 and "could not be reached" in errs[0],
              "an unreachable server gets its own message, replacing the last one rather than stacking",
              f"messages on screen: {errs}")

        state = json.loads(b.js(f"""(()=>{{const b={btn};
            return JSON.stringify({{text: b ? b.textContent.trim() : null, disabled: b ? b.disabled : null,
              badge: document.getElementById('daily-count-badge').textContent.trim(),
              done: document.querySelectorAll('.btn-daily-done').length,
              bg: b ? getComputedStyle(b).backgroundColor : null,
              nextBg: (()=>{{const n=document.querySelector('.daily-exercise-row.is-next .btn-daily-complete');
                              return n ? getComputedStyle(n).backgroundColor : null;}})()}});}})()"""))
        check(state["text"] == "I did this" and state["disabled"] is False,
              "the button is back and tappable for a retry",
              f"text={state['text']!r} disabled={state['disabled']!r}")
        check(state["badge"] == "0/5" and state["done"] == 0,
              "nothing was counted or ticked for a completion that did not save",
              f"badge={state['badge']!r} ticked={state['done']}")
        server = _api(base, "/api/daily", token=token)
        check(server.get("completed_count") == 0,
              "and the server agrees: nothing was recorded",
              f"completed_count={server.get('completed_count')!r}")
        check(state["bg"] in ("rgba(0, 0, 0, 0)", "transparent"),
              "a tapped button does not stay filled purple on a touchscreen (no stuck hover)",
              f"background after the tap was {state['bg']}")
        check(state["nextBg"] not in (None, "rgba(0, 0, 0, 0)", "transparent"),
              "the next-exercise highlight is still filled",
              f"next button background was {state['nextBg']}")

        b.js("window.__completeMode = 'through'")
        touch_tap(b, btn)
        time.sleep(1.8)
        after = json.loads(b.js("""JSON.stringify({
            errs: document.querySelectorAll('.daily-complete-error').length,
            badge: document.getElementById('daily-count-badge').textContent.trim(),
            done: document.querySelectorAll('.btn-daily-done').length})"""))
        check(after["badge"] == "1/5" and after["done"] == 1,
              "a retry that reaches the server is counted",
              f"badge={after['badge']!r} ticked={after['done']}")
        check(after["errs"] == 0,
              "and the old 'Not saved' message is gone once it has saved",
              f"{after['errs']} error message(s) still on screen")

        b.goto(base + "/", wait=3.5)
        reloaded = json.loads(b.js("""JSON.stringify({
            badge: document.getElementById('daily-count-badge').textContent.trim(),
            done: [...document.querySelectorAll('.daily-exercise-row')]
                    .filter(r => r.querySelector('.btn-daily-done'))
                    .map(r => r.querySelector('.daily-exercise-name').textContent.trim())})"""))
        check(reloaded["badge"] == "1/5" and reloaded["done"] == [name],
              "the completion survives a full reload, on the exercise that was tapped",
              f"after reload: badge={reloaded['badge']!r} ticked={reloaded['done']} expected [{name!r}]")
    finally:
        b.call("Emulation.setTouchEmulationEnabled", enabled=False)


def check_celebration_stays_clear_of_the_nav(b: Browser, base: str, app) -> None:
    """A real-phone test: at 5/5 the celebration toasts sat on top of the
    Today / Progress tabs — four in a row on a first mission, about 21 seconds.
    Measured from rendered positions at several phone sizes, through the whole
    sequence and while the page scrolls, and the tabs are tapped by coordinate
    while a toast is up, so "clear" means clear where a thumb goes.
    """
    print("\nThe 5/5 celebration stays above the section nav, at every phone size")
    sizes = [(320, 568), (360, 640), (360, 800), (390, 844), (412, 915)]
    b.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
    sample = """(()=>{const t=document.getElementById('rickie-reaction');
        const n=document.getElementById('pane-nav');
        if(!t || t.hidden) return null;
        const tr=t.getBoundingClientRect(); const nr=n.getBoundingClientRect();
        return JSON.stringify({line: document.getElementById('rickie-reaction-line').textContent.slice(0,40),
          tBottom: tr.bottom, tTop: tr.top, nTop: nr.top, nH: nr.height, vh: innerHeight,
          settled: t.getAnimations().length === 0,
          navOnScreen: nr.height>0 && nr.top<innerHeight && nr.bottom>0,
          tabsReachable: [...n.querySelectorAll('.pane-nav-btn')].filter(x=>x.offsetParent).every(x=>{
            const r=x.getBoundingClientRect(); const h=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
            return !!h && (h===x || x.contains(h));}),
          // Any other control the toast is drawn over must still take the tap.
          coveredButNotTappable: [...document.querySelectorAll('button, a, input, select')].filter(x=>{
            if(!x.offsetParent || t.contains(x)) return false;
            const r=x.getBoundingClientRect(); const cx=r.left+r.width/2, cy=r.top+r.height/2;
            if(cy<tr.top || cy>tr.bottom || cx<tr.left || cx>tr.right || cy<0 || cy>innerHeight) return false;
            const h=document.elementFromPoint(cx, cy); return !h || !(h===x || x.contains(h));
          }).map(x=>(x.textContent||x.className).trim().slice(0,20))});})()"""
    try:
        for w, h in sizes:
            b.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=2, mobile=True)
            _, token = make_user(app, f"toast_{w}x{h}")
            keys = [e["key"] for e in _api(base, "/api/daily", token=token)["exercises"]]
            for k in keys[:4]:
                _api(base, f"/api/daily/{k}/complete", "POST", token=token)
            b.goto(base + "/", wait=1.0)
            b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
            b.goto(base + "/", wait=3.5)

            touch_tap(b, "[...document.querySelectorAll('.btn-daily-complete')]"
                         ".find(x=>x.textContent.trim()==='I did this')")
            seen, overlaps, blocked, lines = 0, [], [], set()
            tapped_tab = None
            start = time.time()
            while time.time() - start < 23:
                el = time.time() - start
                if 7.0 < el < 7.3:
                    b.js("window.scrollTo(0, document.documentElement.scrollHeight)")
                if 12.0 < el < 12.3:
                    b.js("window.scrollTo(0, 0)")
                s = b.js(sample)
                if s:
                    s = json.loads(s)
                    seen += 1
                    lines.add(s["line"])
                    # Only a settled toast counts: the entry and exit animations
                    # slide it 16px, which is motion, not placement.
                    if s["settled"] and s["navOnScreen"] and s["tBottom"] > s["nTop"] + 0.5:
                        overlaps.append(f"{s['line']!r} bottom {s['tBottom']:.0f} > nav top {s['nTop']:.0f}")
                    if not s["tabsReachable"] or s["coveredButNotTappable"]:
                        blocked.append(f"{s['line']!r} {s['coveredButNotTappable']}")
                    if tapped_tab is None and el > 2.0:
                        spot = touch_tap(b, "document.querySelector('.pane-nav-btn[data-pane-target=\"progress\"]')")
                        time.sleep(0.5)
                        tapped_tab = {"spot": spot, "active": b.js(
                            "(document.querySelector('.pane-nav-btn.is-active')||{}).dataset.paneTarget")}
                        b.js("showPane('today')")
                time.sleep(0.25)

            label = f"{w}x{h}"
            check(seen > 0 and len(lines) >= 2, f"{label}: the celebration sequence played",
                  f"saw {seen} samples, lines {lines}")
            check(not overlaps, f"{label}: no toast sits on the section nav",
                  "; ".join(overlaps[:3]))
            check(not blocked, f"{label}: every nav tab, and anything under the toast, stays tappable",
                  f"blocked during {blocked[:2]}")
            ok_tap = bool(tapped_tab) and tapped_tab["spot"].get("onTarget") and tapped_tab["active"] == "progress"
            check(ok_tap, f"{label}: a touch on 'Progress' mid-celebration switches pane",
                  f"{tapped_tab!r}")
    finally:
        b.call("Emulation.setTouchEmulationEnabled", enabled=False)
        b.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)


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
    # The roster identifies people by the name they CHOSE, never by their
    # login. This used to assert the kid's username appeared, which is now
    # precisely the thing that must not happen: registration accepts email
    # addresses and the roster was showing them to every member.
    #
    # So the real journey is driven instead — the kid sets a display name, and
    # the parent sees it — plus the security property, checked on the rendered
    # page rather than on the API response.
    _api(base, "/api/me", "PATCH", kid_token, {"display_name": "Liv"})
    b.js("location.reload()")
    time.sleep(3.0)
    b.js("(()=>{const btn=[...document.querySelectorAll('button')]"
         ".find(b=>b.textContent.trim()==='Open'); if(btn) btn.click(); return 1;})()")
    time.sleep(2.0)
    roster = b.js("(()=>{const e=document.querySelector('.team-roster');"
                  " return e ? e.innerText : '';})()") or ""
    check("Liv" in roster,
          "the kid appears on the roster their parent is reading, by chosen name",
          f"roster was {roster[:140]!r}")
    page = b.js("document.body.innerText") or ""
    check(kid not in page,
          "and the kid's login identifier is nowhere on the parent's screen",
          f"{kid!r} found in the rendered page")

    history = b.js("(()=>{const e=document.querySelector('.team-moments-body');"
                   " return e ? e.innerText : '';})()") or ""
    check(bool(history.strip()) and "Looking back" not in history,
          "team history renders (the moments endpoint has a caller at last)",
          f"history was {history[:120]!r}")

    campfire = b.js("(()=>{const e=document.querySelector('.team-campfire-section');"
                    " return e ? e.innerText : '';})()") or ""
    check("to reach" in campfire, "the campfire shows progress toward its next stage",
          f"campfire was {campfire[:120]!r}")

    # Leaving is the one irreversible control on this panel, and it used to go
    # on one tap with nothing said before or after. A walkthrough was out of
    # the team within 200ms of a single tap: the panel closed, the Team tab
    # disappeared from the nav, and the user landed on Progress in silence.
    first = b.js("(()=>{const b=document.querySelector("
                 "  '.team-panel-leave-row .retention-btn');"
                 " if(!b) return 'no leave button';"
                 " b.click();"
                 " return b.textContent.trim();})()")
    time.sleep(1.2)
    still_in = b.js("(()=>{const r=document.querySelector('.team-roster');"
                    " return !!(r && r.innerText.trim());})()")
    check(bool(still_in), "one tap on Leave Team does not leave the team",
          f"button said {first!r}")
    check("again" in str(first).lower(),
          "and the button says what the next tap will do", str(first))
    note = b.js("(()=>{const n=document.querySelector('.team-panel-leave-note');"
                " return n && !n.hidden ? n.textContent : '';})()") or ""
    check("streak" in note.lower(),
          "and it says the thing somebody hesitating is afraid of", note[:90])


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

    # Correcting and removing it — through the buttons, not the API.
    #
    # Side Quests were write-once: no rename, no delete, and no route to build
    # either on. A typo was permanent and an abandoned habit sat in the list
    # forever. Driven here rather than only in pytest because the previous
    # silent breakage in this section was a UI wiring bug that 295 green tests
    # did not see.
    opened = b.js("(()=>{const e=[...document.querySelectorAll('.challenge-edit-btn')];"
                  " if(!e.length) return 'no edit button';"
                  " e[0].click();"
                  " return document.querySelector('.challenge-editor')"
                  "   ? 'ok' : 'editor did not open';})()")
    if not check(opened == "ok", "a side quest can be edited", str(opened)):
        return
    time.sleep(0.4)

    b.js("(()=>{const i=document.querySelector('.challenge-edit-input');"
         " i.value='Read for 20 minutes';"
         " document.querySelector('.challenge-edit-save').click(); return 1;})()")
    time.sleep(2.5)
    listed = b.js("document.getElementById('challenges-list').innerText") or ""
    check("Read for 20 minutes" in listed and "Read for 10 minutes" not in listed,
          "renaming it sticks", f"list shows {listed[:90]!r}")

    # One tap must NOT delete. The button says what the second tap will do.
    b.js("(()=>{document.querySelector('.challenge-edit-btn').click(); return 1;})()")
    time.sleep(0.4)
    first = b.js("(()=>{const r=document.querySelector('.challenge-edit-remove');"
                 " r.click(); return r.textContent;})()")
    time.sleep(1.2)
    still = b.js("document.getElementById('challenges-list').innerText") or ""
    check("Read for 20 minutes" in still,
          "one tap on Remove does not remove it", f"list shows {still[:90]!r}")
    check("again" in str(first).lower(),
          "and the button says what the next tap will do", str(first))

    b.js("(()=>{document.querySelector('.challenge-edit-remove').click(); return 1;})()")
    time.sleep(2.5)
    after = b.js("document.getElementById('challenges-list').innerText") or ""
    check("Read for 20 minutes" not in after,
          "the second tap removes it", f"list still shows {after[:90]!r}")


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

    # The nav must sit at the BOTTOM of the window, not at the bottom of the
    # content. `position: sticky; bottom: 0` only sticks while its containing
    # block scrolls, so on a short pane the bar stranded itself halfway down
    # with blank space beneath — which reads as a broken layout, not as
    # navigation. Progress on a two-day account is exactly that short pane.
    strand = b.js(
        "(()=>{const n=document.getElementById('pane-nav');"
        " if(!n) return 'no nav';"
        " const r=n.getBoundingClientRect();"
        " const vh=window.innerHeight;"
        " const scrollable=document.documentElement.scrollHeight>vh+2;"
        " return JSON.stringify({gap:Math.round(vh-r.bottom),"
        "   scrollable:scrollable,h:Math.round(r.height)});})()")
    info = json.loads(strand) if str(strand).startswith("{") else {}
    check(info and info.get("gap", 999) <= 2,
          "the pane nav sits at the bottom of the window, not mid-screen",
          f"{info.get('gap')}px of empty space below it "
          f"(page scrollable: {info.get('scrollable')})")

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

    # And it must not open as a blank box.
    #
    # It used to: an empty thread and an "Ask Rickie…" placeholder, with no
    # greeting and nothing saying what he is for. A reviewer sat in front of it
    # and could not think of a question — the ordinary blank-page problem,
    # except here every guess costs a real API call, so the cost of not knowing
    # what to ask is paid in money and in a deflection that teaches somebody he
    # is not much use.
    #
    # The opener is entirely local. This asserts that too: nothing may be sent
    # to /api/coach just by opening the panel.
    time.sleep(0.6)
    opener = (b.js("(()=>{const t=document.querySelector('.coach-thread');"
                   " return t ? t.innerText : '';})()") or "").strip()
    check(len(opener) > 30, "Rickie says something when he opens", opener[:70])

    starters = b.js("(()=>{const s=[...document.querySelectorAll('.coach-starter')];"
                    " return JSON.stringify(s.map(b=>({t:b.textContent.trim(),"
                    "   h:Math.round(b.getBoundingClientRect().height)})));})()")
    rows = json.loads(starters) if str(starters).startswith("[") else []
    check(len(rows) >= 3, "and offers things to tap rather than a blank box",
          f"{len(rows)} starters")
    check(all(r["h"] >= 44 for r in rows),
          "the starters are tappable at phone size",
          str([r["h"] for r in rows]))
    check(all(r["t"].endswith("?") for r in rows),
          "and each one is a question", str([r["t"][:30] for r in rows]))


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

    # And Rickie must not be standing on any of it.
    #
    # The roaming check measures the DEFAULT dashboard. An independent
    # walkthrough kept finding him over Brain Boost specifically — the option
    # buttons, the label, the rep counts — because this whole card only exists
    # after the mission is finished and the question is revealed, which is a
    # page state that check never reaches. The engine is told about content by
    # a MutationObserver, so "it reacts eventually" is the claim; this is the
    # measurement of it, using exactly the same occupancy test.
    b.goto(base + "/", wait=3.0)
    b.js("(()=>{const r=[...document.querySelectorAll('button')]"
         ".find(e=>/Reveal Brain Boost/i.test(e.textContent)); if(r) r.click(); return 1;})()")
    time.sleep(2.0)   # the observer is debounced; give him time to move
    if b.js("document.querySelectorAll('.bb-option-btn').length"):
        blocked = _spots_he_may_stand_in_that_are_not_clear(b, tries=8)
        check(not blocked,
              "Rickie has nowhere to stand that covers the question or its options",
              "; ".join(blocked[:3]))


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


def check_acorns_are_spendable_without_a_team(b: Browser, base: str, app) -> None:
    """The whole acorn loop for somebody who will never join a team.

    Acorns are earned by moving and the only things they buy are photo filters.
    If a solo user cannot reach a filter, the most-earned reward in the product
    is unreachable for the people it is most meant for — so this drives the
    entire loop: balance, an unaffordable filter, a purchase, persistence, and
    the same filter still owned after a reload.
    """
    print("\nAcorns, with no team at all")
    username, token = make_user(app, "acorn")

    from app import User, db
    with app.app_context():
        row = db.session.execute(db.select(User).where(User.username == username)).scalar_one()
        row.acorns_total = 18          # enough for the 15, not the 30
        db.session.commit()
        uid = row.id

    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=2.5)

    # He is in no team. Anything that needs one must not be the only way in.
    teams = b.js("(async()=>0)() , (()=>{return 1;})()")
    del teams

    check(b.js("(()=>{const btn=document.getElementById('journey-make-picture-btn');"
               " return !!(btn);})()") is True or
          b.js("!!document.getElementById('journey-make-picture-btn')") is True,
          "a solo user has a way to make a picture at all")

    spendable = b.js("_acornsAvailable()")
    check(spendable == 18, "the app shows SPENDABLE acorns, not lifetime earned",
          f"showed {spendable}")

    # The catalogue, as the API gives it to a solo user.
    cat = b.js("""(async()=>{const r=await fetch('/api/photo-filters',
        {headers:{Authorization:'Bearer '+localStorage.getItem('streakfit_token')}});
        const j=await r.json(); window.__cat=j; return 'ok';})()""")
    time.sleep(1.2)
    buyable = b.js("(()=>{const c=window.__cat; if(!c) return 'no catalogue';"
                   " const f=(c.filters||c).filter(x=>x.unlock_type==='acorns');"
                   " return f.length;})()")
    check(buyable == 4, "four filters are buyable with acorns", str(buyable))
    del cat

    afford = b.js("(()=>{const c=window.__cat; const f=(c.filters||c)"
                  ".filter(x=>x.unlock_type==='acorns');"
                  " return JSON.stringify(f.map(x=>[x.key,x.cost,x.unlocked]));})()")
    check("false" in str(afford).lower(),
          "and none of them is already owned by a new account", str(afford)[:70])

    # Buy the affordable one through the real endpoint.
    b.js("""(async()=>{const r=await fetch('/api/photo-filters/sweat_mode/unlock',
        {method:'POST',headers:{Authorization:'Bearer '+
        localStorage.getItem('streakfit_token')}});
        window.__buy={status:r.status, body:await r.json()}; return 'ok';})()""")
    time.sleep(1.2)
    status = b.js("window.__buy && window.__buy.status")
    check(status == 200, "a solo user can buy a filter they can afford", str(status))
    left = b.js("window.__buy && window.__buy.body && window.__buy.body.acorns_available")
    check(left == 3, "and the balance goes down by exactly the price", f"left {left}")

    # The one they cannot afford must fail cleanly, and NOT charge them.
    b.js("""(async()=>{const r=await fetch('/api/photo-filters/frosty/unlock',
        {method:'POST',headers:{Authorization:'Bearer '+
        localStorage.getItem('streakfit_token')}});
        window.__poor={status:r.status, body:await r.json()}; return 'ok';})()""")
    time.sleep(1.2)
    pstat = b.js("window.__poor && window.__poor.status")
    check(pstat == 400, "one they cannot afford is refused, not silently ignored",
          str(pstat))
    check(b.js("window.__poor && window.__poor.body && window.__poor.body.error")
          == "not_enough_acorns", "with a reason the UI can explain")

    with app.app_context():
        after = db.session.get(User, uid)
        check(after.acorns_spent == 15,
              "a refused purchase charges nothing", f"spent {after.acorns_spent}")
        check(after.acorns_total == 18,
              "and spending never reduces what they have EARNED",
              f"lifetime {after.acorns_total}")

    # Persistence: still owned after a reload.
    b.goto(base + "/", wait=2.5)
    b.js("""(async()=>{const r=await fetch('/api/photo-filters',
        {headers:{Authorization:'Bearer '+localStorage.getItem('streakfit_token')}});
        const j=await r.json(); window.__cat2=j; return 'ok';})()""")
    time.sleep(1.2)
    owned = b.js("(()=>{const c=window.__cat2; const f=(c.filters||c)"
                 ".find(x=>x.key==='sweat_mode'); return f && f.unlocked;})()")
    check(owned is True, "and it is still owned after a reload")

    # The number on the PROGRESS tab, after spending — which is the screen a
    # person actually looks at, and the one this check previously never
    # exercised. It passed because the fixture had spent nothing, so
    # earned - spent happened to equal earned. An independent reviewer bought a
    # filter and found the composer saying "10 left" while Progress still said
    # 30, through a full reload: /api/me never sent acorns_spent at all, and a
    # missing field reads exactly like zero spent.
    shown = b.js("_acornsAvailable()")
    check(shown == 3,
          "the Progress figure is what is SPENDABLE after a purchase, "
          "not lifetime earned", f"showed {shown} (should be 18 - 15)")
    check(b.js("currentUser && typeof currentUser.acorns_available === 'number'"),
          "and the server sends the spendable figure rather than leaving the "
          "client to infer it")

    # Spending must take two taps. One exploratory tap on a priced chip used to
    # spend the acorns outright, and nothing refunds them.
    src = b.js("String(window._offerFilterPurchase || "
               "(typeof _offerFilterPurchase !== 'undefined' "
               "? _offerFilterPurchase : ''))")
    confirms = ("_pendingFilterKey" in str(src)) and ("Tap it again" in str(src))
    check(confirms,
          "buying a filter asks before it spends, rather than on first tap")


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
        """Type it, then press Save — the way a person does it.

        This used to type the value and dispatch a `change` event, because the
        field once saved on change. The Save button that replaced that
        behaviour landed without this file being touched, so every assertion
        below went on typing into a box nothing was listening to. The feature
        worked the whole time; six checks reported it broken, which is the
        cheaper direction to fail but still a check measuring the wrong thing.

        Clicking the real button is what keeps them honest: if the control is
        renamed or unwired, `set_name` raises here rather than quietly
        producing a page that never saved.
        """
        b.js("(()=>{const i=document.getElementById('display-name-input');"
             f" i.value={json.dumps(value)};"
             " i.dispatchEvent(new Event('input',{bubbles:true}));"
             " const btn=document.getElementById('display-name-save');"
             " if(!btn) throw new Error('no #display-name-save button to press');"
             " btn.click(); return 1;})()")
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

    # Nothing in the settings panel may be clipped by the panel's own width.
    # The active theme button rendered as "🎮 G" at 375px once the buttons
    # gained words, which looks broken rather than terse.
    clipped = b.js("(()=>{const m=document.getElementById('settings-menu');"
                   " if(!m) return 'no menu'; const bad=[];"
                   " const mr=m.getBoundingClientRect();"
                   " for(const el of m.querySelectorAll('button,select,input,label,p')){"
                   "  if(!el.offsetParent) continue;"
                   "  const r=el.getBoundingClientRect();"
                   "  if(r.right>mr.right+1||r.left<mr.left-1)"
                   "    bad.push((el.id||el.className||el.tagName).toString().slice(0,24));"
                   "  if(el.scrollWidth>el.clientWidth+2&&el.tagName!=='SELECT')"
                   "    bad.push('overflow:'+(el.id||el.className||el.tagName).toString().slice(0,20));"
                   " } return bad.join('|');})()")
    check(not clipped, "nothing in the settings panel is clipped at phone width",
          str(clipped)[:90])

    # And the name reaches the app, not just the chat prompt it was built for.
    set_name("Olivia")
    b.goto(base + "/", wait=2.5)
    # Across three consecutive days, not whichever day this happens to run:
    # the name appears on some days by design, so a single-day assertion is a
    # coin flip. What must hold is that it appears at all, and never on a day
    # where the app decided not to use it.
    reaches = b.js("(()=>{if(typeof _withName!=='function') return 'no function';"
                   " const out=[0,1,2].map(d=>_withName('Morning.',d));"
                   " const named=out.filter(s=>/Olivia/.test(s)).length;"
                   " return named===1 ? 'ok' : 'named on '+named+' of 3 days';})()")
    check(reaches == "ok",
          "the name reaches the app's own greeting, on some days not all",
          str(reaches))

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


def check_guest_promise_is_true(b: Browser, base: str) -> None:
    """The guest banner must not promise something the code cannot do.

    It used to read "sign up anytime to save your streak", which was false in
    two independent ways: guest progress lives in a Set in memory, so a reload
    loses it and signing up carries nothing across; and there was no sign-up
    control anywhere in guest mode to act on the offer even if it had been
    true. This checks BOTH halves — that the sentence no longer claims saving,
    and that the way out now exists and lands on the register form — plus the
    fact underneath, that progress genuinely does not survive a reload. If
    carry-over is ever built, this check should fail and be rewritten, not
    deleted.
    """
    b.reset_storage()
    b.goto(base + "/", wait=1.5)
    b.goto(base + "/", wait=2.0)
    b.js("handleGuestMode()")
    time.sleep(2.5)

    text = b.js("(()=>{const g=document.getElementById('guest-mode-banner');"
                " return g ? (g.textContent||'').replace(/\\s+/g,' ').trim() : '';})()")
    if not check(bool(text), "the guest banner is present"):
        return
    promises = bool(re.search(r"save your streak|saved? your progress", str(text), re.I))
    check(not promises, "the guest banner does not promise saving what is not saved",
          str(text))

    # The way out exists, is actually visible, and is big enough to tap.
    btn = b.js("(()=>{const e=document.getElementById('guest-signup-btn');"
               " if(!e) return 'missing';"
               " const r=e.getBoundingClientRect();"
               " if(!(r.width>0&&r.height>0)) return 'not rendered';"
               " if(r.height<28) return 'height '+Math.round(r.height);"
               " const s=getComputedStyle(e);"
               " if(s.visibility==='hidden'||s.display==='none') return 'hidden';"
               " return 'ok';})()")
    check(btn == "ok", "a guest has a reachable way to start a real account",
          str(btn))

    # And it lands on register, not login — a person who has decided to start
    # should not be asked to sign in to an account they do not have.
    landed = b.js("(()=>{handleGuestSignup();"
                  " const f=document.getElementById('register-form');"
                  " const v=document.getElementById('auth-view');"
                  " const shown=f && f.getBoundingClientRect().height>0;"
                  " return (v && !v.hidden && shown) ? 'ok' : 'register form not shown';})()")
    time.sleep(0.5)
    check(landed == "ok", "and it opens the sign-up form, not the login form",
          str(landed))

    # The fact the copy now tells the truth about: a reload is a clean slate.
    b.js("handleGuestMode()")
    time.sleep(2.0)
    # Use the app's own "I did this" button, the way the rest of this file
    # does — a guessed .exercise-card selector matched nothing and turned this
    # assertion into a silent skip, which is worse than not having it.
    tap_and_read(b)
    time.sleep(1.0)
    marked = b.js("(()=>{return (typeof guestCompleted!=='undefined'"
                  " && guestCompleted) ? guestCompleted.size : -1;})()")
    if not check(isinstance(marked, int) and marked > 0,
                 "a guest tap really does register as progress first",
                 f"guestCompleted.size = {marked}"):
        return
    b.goto(base + "/", wait=2.0)
    b.js("handleGuestMode()")
    time.sleep(2.0)
    survived = b.js("(()=>{return (typeof guestCompleted!=='undefined'"
                    " && guestCompleted) ? guestCompleted.size : -1;})()")
    check(survived == 0,
          "and it really is a clean slate on reload, as the banner now says",
          f"guestCompleted.size = {survived} after reload — the banner understates it")


def _spots_he_may_stand_in_that_are_not_clear(b: Browser, tries: int = 8):
    """Place Rickie where the engine says is clear, then measure the page.

    Extracted so it can be pointed at a page STATE as well as at the default
    dashboard. The obstruction bug that kept coming back was never "the engine
    is wrong in general" — it was "the engine is wrong about this screen",
    and the only way to catch that is to run the same measurement after the
    screen has changed.

    Returns a list of descriptions of what he landed on. Empty is good.
    """
    blocked = []
    b.js("RickieRoam.setPaused(true)")   # he must hold still to be measured
    for _ in range(tries):
        # `e.offsetWidth`, never a literal. This was `56`, copied from the CSS
        # of the day. The moment Rickie was resized it placed him at the old
        # size, measured overlap at that wrong position, and would have
        # reported clear — the check surviving the change it exists to police.
        # A harness that hardcodes the number under test cannot see it move.
        b.js("""(()=>{const s=RickieRoam._somewhereClear(); if(!s) return 0;
          const e=document.querySelector('.rickie-roam');
          const st=e.parentNode.getBoundingClientRect();
          const size=e.offsetWidth;
          e.style.transform='translate('+Math.round(s.x*(st.width-size))+'px,'+
            Math.round(s.y*(st.height-size))+'px)'; return 1;})()""")
        hit = b.js("""(()=>{const r=document.querySelector('.rickie-roam').getBoundingClientRect();
          const band=document.getElementById('rickie-roam-band');
          const bad=[];
          const textBearing=(n)=>{for(const c of n.childNodes)
            if(c.nodeType===3&&c.nodeValue&&c.nodeValue.trim()) return true; return false;};
          const nodes=new Set(document.querySelectorAll(
              'button,a,input,select,textarea,img,svg,.bb-option-btn'));
          for(const el of document.body.querySelectorAll('*'))
            if(textBearing(el)) nodes.add(el);
          const rendered=(el)=>{const r=el.getBoundingClientRect();
            if(!r.width||!r.height) return false;
            if(el.offsetParent) return true;
            try{return getComputedStyle(el).position==='fixed';}catch(e){return false;}};
          for(const el of nodes){
            if(!rendered(el)) continue;   // offsetParent is null for fixed
            if(band&&(el===band||band.contains(el))) continue;
            const q=el.getBoundingClientRect();
            if(!q.width||!q.height) continue;
            if(!(q.right<r.left||q.left>r.right||q.bottom<r.top||q.top>r.bottom))
              bad.push((el.id||el.className||el.tagName).toString().slice(0,30));
          } return bad.join('|');})()""")
        if hit:
            blocked.append(hit)
    b.js("RickieRoam.setPaused(false)")
    return blocked


_WHAT_HE_IS_ACTUALLY_ON = """(()=>{
  const e=document.querySelector('.rickie-roam');
  if(!e) return null;
  const r=e.getBoundingClientRect();
  if(!r.width||!r.height) return null;
  // Invisible is not obstructing. He is deliberately hidden until a clear
  // spot exists, and counting that as an overlap would make the fix look
  // like the bug.
  let op=1; try{op=parseFloat(getComputedStyle(e).opacity)||0;}catch(err){}
  if(op<=0.05) return {visible:false, hit:[]};
  const band=document.getElementById('rickie-roam-band');
  const textBearing=(n)=>{for(const c of n.childNodes)
    if(c.nodeType===3&&c.nodeValue&&c.nodeValue.trim()) return true; return false;};
  const nodes=new Set(document.querySelectorAll(
      'button,a,input,select,textarea,img,svg,.bb-option-btn'));
  for(const el of document.body.querySelectorAll('*'))
    if(textBearing(el)) nodes.add(el);
  const rendered=(el)=>{const q=el.getBoundingClientRect();
    if(!q.width||!q.height) return false;
    if(el.offsetParent) return true;
    try{return getComputedStyle(el).position==='fixed';}catch(err){return false;}};
  const bad=[];
  for(const el of nodes){
    if(!rendered(el)) continue;
    if(band&&(el===band||band.contains(el))) continue;
    const q=el.getBoundingClientRect();
    if(!(q.right<r.left||q.left>r.right||q.bottom<r.top||q.top>r.bottom))
      bad.push((el.id||el.className||el.tagName).toString().slice(0,30));
  }
  // Walking is reported, not merged into the verdict. See the caller.
  const walking = !!(window.RickieRoam && RickieRoam._state
                     && RickieRoam._state.walking);
  return {visible:true, hit:bad, walking:walking,
          t:Math.round(performance.now())};
})()"""


def _watch_him_through_a_page_load(b: Browser, base: str, token: str,
                                   samples: int = 45, gap: float = 0.15):
    """Load the page and watch where he ACTUALLY is, from the first frame.

    This is the check the other one could not be. `_spots_he_may_stand_in_that_
    are_not_clear` pauses him and teleports him to coordinates the engine
    nominates, so it only ever asks "is the engine's idea of clear correct?".
    That is worth asking, and it is not the user's question.

    The user's question is "is he on my text right now", and the answer used to
    be yes: for roughly 600ms of every single page load on a phone he sat on
    the exercise names, the reps and the complete button, at the fixed default
    position his starting coordinates produce — while the nominated-spot check
    reported 0/20 clear, because by the time it ran he had already moved.

    Sampling from navigation is what makes that visible. Returns every sample
    where a VISIBLE Rickie overlapped something, with the time it happened.
    """
    b.goto(base + "/", wait=1.0)
    b.reset_storage()
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.call("Page.navigate", url=base + "/")

    seen, standing, travelling = 0, [], []
    for _ in range(samples):
        row = b.js(_WHAT_HE_IS_ACTUALLY_ON)
        if row and row.get("visible"):
            seen += 1
            if row.get("hit"):
                (travelling if row.get("walking") else standing).append(row)
        time.sleep(gap)
    return seen, standing, travelling


def check_rickie_roams(b: Browser, base: str, app) -> None:
    """Rickie, moving, and never in the way.

    Two different questions, and this check needs both:

      1. Is the engine's idea of "clear" actually clear? Answered by placing
         him where it nominates and measuring the real page.
      2. Is he, in fact, ever standing on something? Answered by watching a
         real page load and looking at where he really is.

    (1) alone was the whole check for a long time, and it is the one that can
    pass while the product is broken — it pauses him, moves him somewhere of
    its own choosing, and never observes the position he actually took. A
    character that dodges according to its own map is not the same as a
    character that dodges.
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
    #
    # Zero clear spots is a real page state, not a broken page: about one load
    # in fifteen the Today greeting is a longer variant whose text starts at
    # y=98 and removes the only clear cluster at the top of a 390px screen
    # (measured 2/30). The rule for that state is "out of sight", so that is
    # what is checked then — this used to fail the whole check and skip the
    # rest of it ("0 free", 1 run in 24 on both this code and the last).
    spots = b.js("RickieRoam._freeSpots().length")
    if spots == 0:
        hidden = _wait_for(b, "parseFloat(getComputedStyle(document.querySelector("
                              "'.rickie-roam')).opacity) <= 0.05", 1.5)
        check(hidden, "with nowhere clear at the top of this page, he is out of sight",
              "visible with 0 clear spots")
    else:
        check(True, f"there is somewhere clear for him to stand ({spots})")
        blocked = _spots_he_may_stand_in_that_are_not_clear(b, tries=8)
        check(not blocked, "every position he may stand in is clear of controls AND text",
              "; ".join(blocked[:3]))

    # ...and the same question asked of the page instead of the engine.
    #
    # This is the check that would have caught the page-load obstruction. The
    # one above reported 0/20 clear for it, honestly, because it teleports him
    # to nominated coordinates and never looks at the position he really took.
    _, second_token = make_user(app, "roam_load")
    seen, standing, travelling = _watch_him_through_a_page_load(b, base, second_token)
    # A load with no clear spot anywhere (the long-greeting case above) keeps
    # him hidden throughout — correct under the no-room rule, and nothing to
    # observe. Accepted only when the page confirms there is no room.
    load_no_room = (seen <= 20 and b.js("RickieRoam._state.noRoom") is True
                    and b.js("RickieRoam._freeSpots().length") == 0)
    if load_no_room:
        print(f"    · this load had no clear spot; he stayed hidden "
              f"({seen} visible samples)")
    if check(seen > 20 or load_no_room,
             f"he can be observed through a page load ({seen} samples), or "
             f"is hidden because nowhere is clear"):
        # STANDING on something is the defect, and it is absolute.
        #
        # This is what the nominated-spot check above could not see: he was
        # placed at his default coordinates and shown before /api/daily had
        # rendered anything, so for roughly 600ms of every phone page load he
        # stood on the exercise names, the reps and the complete button. He is
        # now hidden until there is a measured clear spot to appear in.
        worst = sorted({h for row in standing for h in row["hit"]})
        when = f"from t={standing[0]['t']}ms" if standing else ""
        check(not standing,
              "he is never standing on anything while visible, including "
              "during the load",
              f"{len(standing)}/{seen} samples {when}: {', '.join(worst[:4])}")

        # WALKING over something is not the same claim, and pretending it is
        # would make this check a liar in the other direction.
        #
        # The clear spots he moves between are separated by content — that is
        # what "clear spot" means on a page this full — so a character who
        # walks must cross things to get anywhere. Measured over 408 visible
        # samples at 320px: 0 standing overlaps and 28 while travelling, all
        # inside a single crossing. Forbidding it outright would mean either a
        # character who never moves or a check that fails at random.
        #
        # So it is reported every run, and bounded: more than half his visible
        # life spent crossing text is not travel, it is living there.
        share = len(travelling) / seen if seen else 0
        print(f"    · in transit over content for {len(travelling)}/{seen} "
              f"samples ({share:.0%}) — inherent to a roaming character, "
              f"bounded not forbidden")
        check(share <= 0.60,
              "and he is only ever passing over content, not living on it",
              f"{share:.0%} of visible samples")

    # Hidden-until-clear must not become hidden-forever. He is a companion;
    # one who never turns up is a worse bug, and a much quieter one, than the
    # 600ms of covering this replaced.
    #
    # Polled, not sampled once at 3.5s. A single sample failed ~1 run in 12 on
    # BOTH this release and 4700708, because he is legitimately out of sight
    # for a few seconds at a time mid-peek or after slipping off an edge. The
    # question is whether he turns up, so wait for that — ten seconds covers
    # the longest hide a behaviour produces — and exit the moment he does.
    b.goto(base + "/", wait=1.0)
    shown = None
    for _ in range(45):
        shown = b.js("""(()=>{const e=document.querySelector('.rickie-roam');
          if(!e) return null;
          return parseFloat(getComputedStyle(e).opacity) > 0.05;})()""")
        if shown is True:
            break
        time.sleep(0.2)
    free_now = b.js("RickieRoam._freeSpots().length")
    no_room = b.js("RickieRoam._state.noRoom") is True and free_now == 0
    if shown is not True and no_room:
        # The one legitimate reason to stay out of sight: nowhere to stand
        # (see the greeting note above). The return path is covered by
        # check_rickie_is_never_left_on_content.
        print("    · this load had no clear spot at all; he stayed hidden, as he should")
    check(shown is True or no_room,
          "and he does actually turn up after the page settles",
          f"opacity says visible={shown} after 10s; free spots {free_now}")

    # He must be wholly on screen. A walkthrough found him in ONE position for
    # 11 of 23 samples with half his body past the left edge — which reads as a
    # rendering bug, not a character. Checked over many placements, because a
    # single sample would usually miss it.
    offscreen = b.js("""(()=>{const el=document.querySelector('.rickie-roam');
      const bad=[];
      for(let i=0;i<25;i++){
        const s=RickieRoam._somewhereClear&&RickieRoam._somewhereClear();
        if(!s) continue;
        RickieRoam._state.x=s.x; RickieRoam._state.y=s.y; RickieRoam._place();
        const r=el.getBoundingClientRect();
        if(r.left< -0.5||r.top< -0.5||r.right>innerWidth+0.5||r.bottom>innerHeight+0.5)
          bad.push(Math.round(r.left)+','+Math.round(r.top));
      } return bad.join('|');})()""")
    check(not offscreen, "he is never rendered partly off the screen",
          str(offscreen)[:80])

    # He must step aside when content appears UNDER him, not only when the
    # page scrolls. Placement used to be checked at move time only, so anything
    # rendering beneath a standing Rickie left him on top of it — a walkthrough
    # caught him on an exercise illustration and on the acorns explanation,
    # both reached without scrolling.
    # NOT gated on him standing still any more.
    #
    # An earlier version of this check waited for `walking` to be false before
    # planting content, because mid-walk he deliberately ignores the step-aside
    # and the check failed about one run in four. That made the check pass, and
    # it was the wrong fix: the flakiness was real. Content arriving during a
    # walk left him standing on it afterwards, because nothing re-ran the
    # handler when the walk ended, and the "wait for stillness" dance simply
    # arranged for the test never to meet that case. walkTo now re-checks on
    # arrival, so this plants content whenever it likes and polls for him to
    # move — which exercises both paths instead of only the easy one.

    moved = b.js("""(()=>{const el=document.querySelector('.rickie-roam');
      const r=el.getBoundingClientRect();
      const before=r.left+','+r.top;
      // Drop a block of text exactly where he is standing.
      const d=document.createElement('div');
      d.id='uicheck-intruder';
      d.textContent='content that arrived underneath him';
      d.style.cssText='position:fixed;z-index:1;left:'+Math.round(r.left)+
        'px;top:'+Math.round(r.top)+'px;width:'+Math.round(r.width)+
        'px;height:'+Math.round(r.height)+'px;background:#fff;';
      document.body.appendChild(d);
      return before;})()""")
    # Poll rather than sleep once: the debounce is 250ms and the move 420ms,
    # but if he was mid-walk when the content arrived he re-checks on arrival,
    # and a walk can be a couple of seconds. Six seconds is far longer than
    # either path needs and the loop exits the moment he is off it.
    #
    # Moving is not the only right answer. At 390px a 64px Rickie has one small
    # cluster of clear spots, and a block dropped on him near its middle leaves
    # NOWHERE clear — this check used to fail then (2 runs in 24, "stayed at
    # 206,29"), because he stayed on the block. The rule now is: move if there
    # is room, hide if there is not. Both are accepted; standing on it is not.
    outcome, after, free = None, str(moved), None
    for _ in range(30):
        time.sleep(0.2)
        row = b.js("""(()=>{const e=document.querySelector('.rickie-roam');
          const r=e.getBoundingClientRect();
          return {at:r.left+','+r.top,
                  op:parseFloat(getComputedStyle(e).opacity),
                  noRoom:!!RickieRoam._state.noRoom,
                  free:RickieRoam._freeSpots().length};})()""")
        after, free = row["at"], row["free"]
        if row["op"] <= 0.05 and row["noRoom"]:
            outcome = "hid"
            break
        if after != str(moved) and row["op"] > 0.05:
            outcome = "moved"
            break
    b.js("(()=>{const d=document.getElementById('uicheck-intruder');"
         " if(d) d.remove(); return 1;})()")
    print(f"    · content under him: he {outcome or 'stayed'} "
          f"({free} clear spot(s) left with it there)")
    check(outcome is not None,
          "he steps aside when content appears under him, without a scroll — "
          "or hides when nowhere is clear",
          f"stayed at {after}, {free} clear spot(s)")

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


_RICKIE_JS = """
  window.__rk = {
    el: () => document.querySelector('.rickie-roam'),
    visible: () => parseFloat(getComputedStyle(__rk.el()).opacity) > 0.05,
    at: () => { const r=__rk.el().getBoundingClientRect();
                return Math.round(r.left)+','+Math.round(r.top); },
    plantOn: (id) => { const r=__rk.el().getBoundingClientRect();
      const d=document.createElement('div'); d.id=id;
      d.textContent='content that arrived underneath him';
      d.style.cssText='position:fixed;z-index:1;background:#fff;left:'+
        Math.round(r.left)+'px;top:'+Math.round(r.top)+'px;width:'+
        Math.round(r.width)+'px;height:'+Math.round(r.height)+'px;';
      document.body.appendChild(d); return __rk.at(); },
    cover: (id) => { const d=document.createElement('div'); d.id=id;
      d.textContent='a screen with no room anywhere '.repeat(40);
      d.style.cssText='position:fixed;inset:0;z-index:1;background:#fff;'+
        'font-size:14px;overflow:hidden;';
      document.body.appendChild(d); return 1; },
    drop: (id) => { const d=document.getElementById(id); if(d) d.remove(); return 1; },
  }; 1;
"""


def _rickie_obstructing(b: Browser) -> list:
    """What a VISIBLE Rickie overlaps, measured from the page, not his map."""
    row = b.js(_WHAT_HE_IS_ACTUALLY_ON)
    return (row or {}).get("hit") or [] if (row or {}).get("visible") else []


def _watch_rickie(b: Browser, seconds: float, gap: float = 0.15) -> dict:
    """Sample for `seconds`: how often he was visible, and every obstruction,
    with its time in ms from the start of the watch."""
    seen, standing, t0 = 0, [], time.time()
    while time.time() - t0 < seconds:
        row = b.js(_WHAT_HE_IS_ACTUALLY_ON) or {}
        if row.get("visible"):
            seen += 1
            if row.get("hit") and not row.get("walking"):
                standing.append((round((time.time() - t0) * 1000), row["hit"][:3]))
        time.sleep(gap)
    return {"seen": seen, "standing": standing}


# How long he may still be on content after the page moves under him: the
# 250ms step-aside debounce (scroll and mutation storms are coalesced) plus
# the 260ms opacity fade when he hides, measured at ~0.5s. Bounded here so a
# slow or missing reaction fails, while the reaction itself is not a defect.
_RICKIE_REACTION_MS = 800


def _wait_for(b: Browser, js: str, seconds: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        if b.js(js) is True:
            return True
        time.sleep(0.1)
    return False


def _appears_where_there_is_room(b: Browser, seconds: float) -> bool:
    """Wait for him to be visible. If he is hidden because this screen truly
    has no clear spot (the long-greeting load), scroll to where there is room
    — he must then come back — and return to the top."""
    if _wait_for(b, "__rk.visible()", seconds):
        return True
    if b.js("RickieRoam._state.noRoom === true && "
            "RickieRoam._freeSpots().length === 0") is not True:
        return False
    b.js("window.scrollTo(0,240)")
    ok = _wait_for(b, "__rk.visible()", 4.0)
    b.js("window.scrollTo(0,0)")
    time.sleep(0.6)
    return ok


def check_rickie_is_never_left_on_content(b: Browser, base: str, app) -> None:
    """Owner-approved rule, Sept 2026: never stay on workout text or controls.

    Move to a clear spot if there is one; if there is none, hide; come back
    when there is room; at page load with no room, stay hidden rather than
    appear on content. Holds in every mode he has — roaming, settled, reduced
    motion, Quiet, Minimal — while keeping 64px and pointer-events: none.

    The previous rule revealed him anyway after 2.5s and left him where he was
    when nowhere was clear. At 390px, 64px, that was reachable by scrolling
    120px: zero clear spots, and him standing on the exercise text.
    Obstruction is always measured from the rendered page
    (_WHAT_HE_IS_ACTUALLY_ON), never from the engine's own map.
    """
    print("\nRickie — never left on content")
    _, token = make_user(app, "rk_room")
    b.goto(base + "/", wait=1.0)
    b.reset_storage()
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=1.0)
    b.js(_RICKIE_JS)
    appeared = _appears_where_there_is_room(b, 10)
    if not check(appeared, "he appears on a normal load (or once there is room)",
                 str(b.js("""(()=>{const s=RickieRoam._state;return {noRoom:s.noRoom,
                   free:RickieRoam._freeSpots().length,x:s.x,y:s.y,walking:s.walking,
                   paused:s.paused,cls:__rk.el().className,
                   op:getComputedStyle(__rk.el()).opacity,scrollY:scrollY};})()"""))):
        return

    # Size and taps: unchanged by any of this.
    size = b.js("__rk.el().offsetWidth")
    check(size == 64, "he is still 64px at phone width", f"{size}px")
    pe = b.js("getComputedStyle(document.getElementById('rickie-roam-band')).pointerEvents")
    check(pe == "none", "and still never intercepts a tap", f"pointer-events: {pe}")

    # 1. Stationary, content arrives, room exists → he moves, stays visible.
    #    Made deterministic: stand him at the LEFTMOST clear spot, so a block
    #    on him leaves the far end of the cluster clear.
    #    Behaviours are swapped for a long no-op for this scenario only, so no
    #    walk can start mid-measurement; a walk already under way is waited out.
    b.js("""(()=>{RickieRoam._behaviours.forEach(function(x){
      x._run=x.run; x.run=function(done){setTimeout(done,60000);};}); return 1;})()""")
    _wait_for(b, "RickieRoam._state.walking === false", 8.0)
    #    "Room elsewhere" is constructed, not assumed. On the live page the
    #    clear cluster is often narrower than the ~140px a block on him
    #    removes, so the same setup sometimes leaves no room — the case
    #    scenario 2 covers. Here the dashboard content is taken out of layout
    #    (display:none; his layer lives outside it), leaving the screen open:
    #    a block on him then always leaves room, and he must MOVE, not hide.
    #    The real-page version (move or hide) is check_rickie_roams.
    b.js("document.getElementById('dashboard-view').style.display='none'")
    time.sleep(0.6)
    b.js("""(()=>{const st=RickieRoam._state; st.x=0.5; st.y=0.5;
      __rk.el().style.transition=''; RickieRoam._place(); return 1;})()""")
    time.sleep(0.4)
    before = b.js("__rk.plantOn('rk-block')")
    free_with = b.js("RickieRoam._freeSpots().length")
    moved = _wait_for(b, f"__rk.visible() && __rk.at() !== {json.dumps(before)}", 2.0)
    time.sleep(0.6)   # let the 420ms step-aside finish before measuring
    hit = _rickie_obstructing(b)
    check(free_with > 0 and moved and not hit,
          "content lands under a stationary Rickie with room elsewhere: he moves, "
          "and is visible and clear",
          f"free={free_with} moved={moved} on={hit}")
    b.js("__rk.drop('rk-block')")
    b.js("document.getElementById('dashboard-view').style.display=''")
    time.sleep(0.4)
    b.js("""(()=>{RickieRoam._behaviours.forEach(function(x){
      if(x._run){x.run=x._run; delete x._run;}}); return 1;})()""")

    # 2. No clear location anywhere → he hides.
    b.js("__rk.cover('rk-cover')")
    hid = _wait_for(b, "!__rk.visible() && RickieRoam._state.noRoom === true", 1.5)
    check(hid, "with nowhere clear on screen, he hides",
          f"visible={b.js('__rk.visible()')} free={b.js('RickieRoam._freeSpots().length')}")
    watch = _watch_rickie(b, 3.0)
    check(watch["seen"] == 0,
          "and stays hidden while there is no room — no behaviour brings him back",
          f"visible in {watch['seen']} samples")

    # 3. Room returns → he comes back, somewhere clear.
    b.js("__rk.drop('rk-cover')")
    back = _appears_where_there_is_room(b, 3.0)
    time.sleep(0.5)
    check(back and not _rickie_obstructing(b),
          "when room returns, he comes back — onto a clear spot",
          f"visible={back} on={_rickie_obstructing(b)}")

    # 4. Scrolling. At 390px a 64px Rickie has no clear spot at all ~120px down;
    #    that was where he used to stand on the exercise text.
    stood = []
    for y in (60, 120, 180, 240, 120, 0):
        b.js(f"window.scrollTo(0,{y})")
        time.sleep(0.9)
        on = _rickie_obstructing(b)
        if on:
            stood.append(f"scrollY {y}: {on[:3]}")
    check(not stood, "scrolling never leaves him standing on content", "; ".join(stood))
    check(_appears_where_there_is_room(b, 4.0),
          "and after scrolling he is visible again wherever there is room")

    # 5. First load with no room at all → hidden throughout, never shown on
    #    content; then he appears once room exists.
    ident = b.call("Page.addScriptToEvaluateOnNewDocument", source=(
        "document.addEventListener('DOMContentLoaded',function(){"
        "var d=document.createElement('div');d.id='rk-load-cover';"
        "d.textContent='a screen with no room anywhere '.repeat(40);"
        "d.style.cssText='position:fixed;inset:0;z-index:1;background:#fff;"
        "font-size:14px;overflow:hidden;';document.body.appendChild(d);});"))
    try:
        b.call("Page.navigate", url=base + "/")
        watch = _watch_rickie(b, 4.5)   # past the old 2.5s reveal cap
        check(watch["seen"] == 0,
              "a page that loads with no clear spot keeps him hidden — past the "
              "old 2.5s cap that showed him on content",
              f"visible in {watch['seen']} samples, standing {watch['standing'][:2]}")
    finally:
        b.call("Page.removeScriptToEvaluateOnNewDocument",
               identifier=ident.get("identifier"))
    b.js(_RICKIE_JS)
    b.js("__rk.drop('rk-load-cover')")
    back = _appears_where_there_is_room(b, 4.0)
    time.sleep(0.5)
    check(back and not _rickie_obstructing(b),
          "and appears, somewhere clear, once there is room",
          f"visible={back} on={_rickie_obstructing(b)}")

    # 6. Settled ("Ask Rickie to settle"): still moves out of the way, with no
    #    travel animation, and stays settled.
    b.js("RickieRoam.setPaused(true)")
    time.sleep(0.3)
    b.js("__rk.plantOn('rk-block')")
    time.sleep(1.0)
    off = not _rickie_obstructing(b)
    trans = b.js("getComputedStyle(__rk.el()).transitionDuration")
    check(off and b.js("RickieRoam.isPaused()") is True,
          "settled, he still gets out from under new content, and stays settled",
          f"clear={off} paused={b.js('RickieRoam.isPaused()')}")
    check(all(float(t.strip().rstrip("s") or 0) == 0 for t in str(trans).split(",")),
          "without a travel animation", f"transition-duration {trans}")
    b.js("__rk.drop('rk-block')")
    b.js("RickieRoam.setPaused(false)")

    # 7. Reduced motion: never roams; still never left on content.
    b.call("Emulation.setEmulatedMedia",
           features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
    try:
        b.goto(base + "/", wait=1.0)
        b.js(_RICKIE_JS)
        if check(_appears_where_there_is_room(b, 6.0),
                 "reduced motion: he is still there"):
            here = b.js("__rk.at()")
            time.sleep(4.0)
            check(b.js("__rk.at()") == here and b.js("RickieRoam.isPaused()") is True,
                  "and does not roam", f"{here} -> {b.js('__rk.at()')}")
            b.js("__rk.plantOn('rk-block')")
            time.sleep(1.0)
            check(not _rickie_obstructing(b),
                  "and still gets out from under new content",
                  str(_rickie_obstructing(b)))
            b.js("__rk.drop('rk-block')")
            b.js("window.scrollTo(0,120)")
            time.sleep(1.0)
            check(not _rickie_obstructing(b),
                  "and is not left on content by a scroll either",
                  str(_rickie_obstructing(b)))
    finally:
        b.call("Emulation.setEmulatedMedia", features=[])

    # 8. Quiet and Minimal: the same rule. (The app has Full/Quiet/Minimal plus
    #    the roaming toggle above; there is no separate "Hidden" mode.)
    for mode in ("quiet", "minimal"):
        r = _api(base, "/api/me", "PATCH", token, {"rickie_mode": mode})
        b.goto(base + "/", wait=1.0)
        b.js(_RICKIE_JS)
        b.js("window.scrollTo(0,120)")
        watch = _watch_rickie(b, 3.0)
        b.js("window.scrollTo(0,0)")
        late = [s_ for s_ in watch["standing"] if s_[0] > _RICKIE_REACTION_MS]
        react = max([s_[0] for s_ in watch["standing"]], default=0)
        print(f"    · {mode}: on content for up to {react}ms after the scroll "
              f"({len(watch['standing'])} sample(s)), then clear")
        check("__status__" not in (r or {}) and not late,
              f"{mode.capitalize()} mode: never left on content "
              f"(clear within {_RICKIE_REACTION_MS}ms of a scroll)",
              f"api={r if '__status__' in (r or {}) else 'ok'} late={late[:2]}")
    _api(base, "/api/me", "PATCH", token, {"rickie_mode": "full"})


def check_appeals_are_reachable(b: Browser, base: str, app) -> None:
    """A decision you can see, and an appeal you can actually file.

    This check exists because the feature shipped unreachable. The endpoints,
    the model, the migration and the tests were all correct, and the settings
    row that opened them was lost in a failed `git stash pop` -- leaving
    openModerationDecisions() as a function nothing called. Every pytest test
    still passed, because pytest talks to the API and never opens the menu.

    So this drives it the way the one person who needs it would: somebody with
    a decision against their account, going looking for what happened and
    whether they can say anything about it.
    """
    print("\nDecisions and appeals")
    from app import ModerationAction, User, db as _db

    username, token = make_user(app, "appeal")
    with app.app_context():
        user = _db.session.execute(
            _db.select(User).where(User.username == username)).scalar_one()
        # A decision with no report attached: report_id is nullable precisely
        # because an operator action is not always the end of a report.
        _db.session.add(ModerationAction(
            actor='operator', action='suspend_social',
            target_user_id=user.id, note='uicheck fixture'))
        _db.session.commit()

    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=2.5)

    b.js("(()=>{const m=document.getElementById('settings-menu');"
         " if(m) m.hidden=false; return 1;})()")
    time.sleep(0.8)

    # 1. The row is THERE. This is the assertion the whole check exists for.
    if not check(bool(b.js("(()=>{const r=document.getElementById("
                           "'settings-row-moderation'); return !!(r && !r.hidden);})()")),
                 "somebody with a decision can find it in settings"):
        return

    # 2. The panel starts closed and the button says so.
    check(b.js("document.getElementById('btn-moderation-decisions')"
               ".getAttribute('aria-expanded')") == "false",
          "the control reports itself closed before it is pressed")

    # 3. Pressing it shows the decision in words, not a code.
    b.js("document.getElementById('btn-moderation-decisions').click()")
    time.sleep(1.4)
    text = (b.js("(()=>{const p=document.getElementById('moderation-panel');"
                 " return p && !p.hidden ? p.innerText : '';})()") or "").strip()
    check("Posting to teams is paused" in text,
          "it says what happened in plain language", text[:120])
    check("suspend_social" not in text,
          "and not the internal action name", text[:120])
    check(b.js("document.getElementById('btn-moderation-decisions')"
               ".getAttribute('aria-expanded')") == "true",
          "the control now reports itself open")

    # 4. aria-controls has to point at something real, or it is noise.
    controls = b.js("document.getElementById('btn-moderation-decisions')"
                    ".getAttribute('aria-controls')")
    check(bool(b.js(f"!!document.getElementById({json.dumps(controls)})")),
          "aria-controls points at an element that exists", str(controls))

    # 5. The appeal box is labelled. A bare textarea is unusable by anyone
    #    who cannot see where it sits on the page.
    labelled = b.js("(()=>{const t=document.querySelector("
                    "'.moderation-appeal-input'); if(!t) return '';"
                    " const l=document.querySelector(`label[for='${t.id}']`);"
                    " return l ? l.textContent.trim() : '';})()")
    check(bool(labelled), "the appeal box has a real label", str(labelled))

    # 6. Filing one works, and the answer promises a person -- not a verdict.
    b.js("(()=>{const t=document.querySelector('.moderation-appeal-input');"
         " t.value='I would like this looked at again.'; return 1;})()")
    b.js("document.querySelector('.moderation-appeal-btn').click()")
    time.sleep(1.8)
    after = (b.js("document.getElementById('moderation-panel').innerText") or "").strip()
    check("Someone will look at this" in after,
          "filing one is acknowledged without promising an outcome", after[:160])

    # 7. It survives a reload as submitted, rather than offering the form again.
    b.goto(base + "/", wait=2.5)
    b.js("(()=>{const m=document.getElementById('settings-menu');"
         " if(m) m.hidden=false; return 1;})()")
    time.sleep(0.5)
    b.js("document.getElementById('btn-moderation-decisions').click()")
    time.sleep(1.4)
    again = (b.js("document.getElementById('moderation-panel').innerText") or "").strip()
    check("Appeal submitted" in again or "Someone will look at this" in again,
          "and it is still submitted after a reload", again[:160])
    check(not b.js("!!document.querySelector('.moderation-appeal-btn')"),
          "the form is not offered a second time for the same decision")

    # 8. A second press closes it again.
    b.js("document.getElementById('btn-moderation-decisions').click()")
    time.sleep(0.6)
    check(bool(b.js("document.getElementById('moderation-panel').hidden")),
          "pressing it again closes the panel")


def check_appeals_stay_hidden_for_everybody_else(b: Browser, base: str, app) -> None:
    """The other half, and the one that protects the other 99.9%.

    A standing "Appeals" row in the settings menu of a family movement app
    tells everyone who opens it that being moderated is a thing that happens
    here. It has to be absent, not merely empty, for anyone with a clean
    account.
    """
    print("\nNo accusation for people with nothing against them")
    _username, token = make_user(app, "clean")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=2.5)
    b.js("(()=>{const m=document.getElementById('settings-menu');"
         " if(m) m.hidden=false; return 1;})()")
    time.sleep(1.2)

    check(bool(b.js("(()=>{const r=document.getElementById('settings-row-moderation');"
                    " return !!(r && r.hidden);})()")),
          "a clean account never sees the row")
    menu = (b.js("document.getElementById('settings-menu').innerText") or "")
    check("appeal" not in menu.lower(),
          "and the word 'appeal' appears nowhere in their settings", menu[:160])


def check_accessibility_basics(b: Browser, base: str, app) -> None:
    """The things a screen reader and a keyboard need, on the real page.

    Not an accessibility audit — no automated check is one. These are the four
    failures that are objectively decidable from the DOM and that make a control
    unusable rather than merely awkward: a control with no accessible name, an
    image with no alt and no aria-hidden, an input with no label, and a heading
    level skipped. The theme buttons shipped as three bare emoji with a title
    attribute, which a phone never shows and a screen reader reads as
    "clipboard", and nothing here caught it.
    """
    print("\nAccessibility — names, labels, headings")
    username, token = make_user(app, "a11y")
    b.goto(base + "/", wait=1.0)
    b.js(f"localStorage.setItem('streakfit_token', {json.dumps(token)})")
    b.goto(base + "/", wait=2.5)
    # Open the panels that are hidden by default, so their controls count too.
    b.js("(()=>{const m=document.getElementById('settings-menu');"
         " if(m) m.hidden=false; return 1;})()")
    time.sleep(0.5)

    nameless = b.js("""(()=>{const bad=[];
      const named=(el)=>{
        if(el.getAttribute('aria-label')) return true;
        if(el.getAttribute('aria-labelledby')) return true;
        if((el.textContent||'').trim()) return true;
        if(el.getAttribute('title')) return true;
        if(el.tagName==='INPUT'&&el.getAttribute('placeholder')) return true;
        const id=el.id;
        if(id&&document.querySelector('label[for="'+CSS.escape(id)+'"]')) return true;
        if(el.closest('label')) return true;
        return false;};
      for(const el of document.querySelectorAll('button,a[href],select,textarea,input')){
        if(!el.offsetParent) continue;
        if(el.type==='hidden') continue;
        if(!named(el)) bad.push((el.id||el.className||el.tagName).toString().slice(0,26));
      } return bad.join('|');})()""")
    check(not nameless, "every visible control has an accessible name",
          str(nameless)[:100])

    imgless = b.js("""(()=>{const bad=[];
      for(const el of document.querySelectorAll('img')){
        if(!el.offsetParent) continue;
        if(el.getAttribute('aria-hidden')==='true') continue;
        if(el.hasAttribute('alt')) continue;
        bad.push((el.id||el.getAttribute('src')||'img').toString().slice(0,34));
      } return bad.join('|');})()""")
    check(not imgless, "every visible image has alt text or is marked decorative",
          str(imgless)[:100])

    unlabelled = b.js("""(()=>{const bad=[];
      for(const el of document.querySelectorAll('input,select,textarea')){
        if(!el.offsetParent||el.type==='hidden') continue;
        const id=el.id;
        const hasLabel=(id&&document.querySelector('label[for="'+CSS.escape(id)+'"]'))
          ||el.closest('label')||el.getAttribute('aria-label')
          ||el.getAttribute('aria-labelledby');
        if(!hasLabel) bad.push((el.id||el.name||el.type).toString().slice(0,26));
      } return bad.join('|');})()""")
    check(not unlabelled, "every visible form field has a label",
          str(unlabelled)[:100])

    headings = b.js("""(()=>{const seen=[];
      for(const h of document.querySelectorAll('h1,h2,h3,h4,h5,h6')){
        if(!h.offsetParent) continue;
        seen.push(parseInt(h.tagName[1],10));}
      let prev=0, bad=[];
      for(const lvl of seen){ if(prev&&lvl>prev+1) bad.push(prev+'->'+lvl); prev=lvl; }
      return bad.join(',');})()""")
    check(not headings, "heading levels are not skipped", str(headings)[:60])

    # Keyboard: the primary action must be reachable and show focus.
    focusable = b.js("""(()=>{const n=document.querySelectorAll(
      'button:not([disabled]),a[href],input:not([type=hidden]),select,textarea');
      let c=0; for(const el of n) if(el.offsetParent) c++; return c;})()""")
    check(focusable > 0, f"the page has keyboard-reachable controls ({focusable})")


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


# WCAG contrast of an element's text against what is actually behind it.
#
# The background is the first non-transparent one walking up the ancestors: a
# transparent button (.mod-open at rest) is read against the page it sits on,
# not against "transparent", which would parse as black and flatter a light
# label. Opacity on the element or an ancestor is not composited — no control
# measured here uses it outside :disabled, which WCAG exempts.
_CONTRAST_JS = (
    "function lum(c){var m=c.match(/[\\d.]+/g).slice(0,3).map(function(v){"
    "v=v/255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);});"
    "return 0.2126*m[0]+0.7152*m[1]+0.0722*m[2];}"
    "function bgOf(n){for(;n&&n.nodeType===1;n=n.parentElement){"
    "var b=getComputedStyle(n).backgroundColor,a=b.match(/[\\d.]+/g);"
    "if(a&&(a.length<4||parseFloat(a[3])>0.99))return b;}return 'rgb(255,255,255)';}"
    "function contrastOf(n){var s=getComputedStyle(n),bg=bgOf(n),"
    "a=lum(s.color),g=lum(bg),r=(Math.max(a,g)+0.05)/(Math.min(a,g)+0.05);"
    "return {ratio:Math.round(r*100)/100, bg:bg, fg:s.color};}"
)


def _hovered_contrast(b: Browser, selector: str) -> dict | None:
    """Contrast of the first `selector` in its :hover state.

    Forced through the DevTools CSS domain. A synthetic mouse move does not
    reliably apply :hover here, because the browser emulates a touch phone;
    forcing the pseudo-class exercises the same stylesheet rule. `changed`
    proves the hover rule actually applied rather than measuring rest twice.
    """
    rest = b.js("(function(){" + _CONTRAST_JS + "var e=document.querySelector("
                + json.dumps(selector) + ");return e?contrastOf(e):null;})()")
    if not rest:
        return None
    b.call("DOM.enable")
    b.call("CSS.enable")
    root = b.call("DOM.getDocument")["root"]["nodeId"]
    node = b.call("DOM.querySelector", nodeId=root, selector=selector)["nodeId"]
    b.call("CSS.forcePseudoState", nodeId=node, forcedPseudoClasses=["hover"])
    try:
        got = b.js("(function(){" + _CONTRAST_JS + "return contrastOf("
                   "document.querySelector(" + json.dumps(selector) + "));})()")
    finally:
        b.call("CSS.forcePseudoState", nodeId=node, forcedPseudoClasses=[])
    got["hovered"] = got["bg"] != rest["bg"]
    return got


def check_moderation_operator_can_close_a_report(b: Browser, base: str, app) -> None:
    """The workflow that did not exist until 2026-09-22.

    The moderation API was complete, and /admin called six endpoints — none of
    them the reports API. A child-safety alert said "Open <url>/admin to review
    it" and led to a page where nothing could be done. Every API test passed.

    So this drives the real page: load the queue, open a report, arm a
    disposition, cancel it, arm it again, confirm, and read back the status the
    page then displays. A synthetic report created locally, never production.
    """
    import uuid as _uuid

    secret = os.environ.get("ADMIN_SECRET") or "uicheck-local-secret"
    os.environ["ADMIN_SECRET"] = secret

    # A local synthetic report, written straight to the local database.
    with app.app_context():
        from app import db, Report, User
        reporter = User.query.filter(User.username.like("uicheck_mod_%")).first()
        if reporter is None:
            _u, _t = make_user(app, "mod_reporter")
            reporter = User.query.filter_by(username=_u).first()
        pid = _uuid.uuid4().hex
        r = Report(public_id=pid, reporter_user_id=reporter.id,
                   reported_user_id=reporter.id, category="child_safety",
                   subject_type="user", status="pending",
                   created_at=dt.datetime.utcnow(),
                   due_at=dt.datetime.utcnow() + dt.timedelta(hours=24))
        db.session.add(r)
        db.session.commit()

    b.goto(f"{base}/admin", wait=2.0)

    # 11. Unauthorized: the queue must hold nothing before a secret is entered.
    pre = b.js("document.getElementById('moderation-queue').textContent")
    check(pid[:12] not in str(pre),
          "unauthenticated /admin does not disclose report ids",
          f"queue text was: {str(pre)[:120]}")

    # 2. Authenticate through the existing mechanism. The value is typed into
    # the page, never placed in a URL where it would reach a log.
    b.js("document.getElementById('secret-input').value = "
         + json.dumps(secret) + "; loadAll();")
    time.sleep(2.5)

    # 1 & 3. The queue is visible and lists the report.
    qtext = str(b.js("document.getElementById('moderation-queue').textContent"))
    check("Moderation" in str(b.js("document.body.textContent")),
          "the moderation section is present on /admin")
    check(pid[:12] in qtext, "the pending report appears in the queue",
          f"queue text: {qtext[:200]}")
    check("OVERDUE" in qtext or "due in" in qtext,
          "the queue shows a deadline")

    # 3. Open it.
    b.js("openModerationReport(" + json.dumps(pid) + ")")
    time.sleep(1.5)
    detail = str(b.js("document.getElementById('moderation-detail').textContent"))

    # 4. Details, urgency, evidence and history render.
    check("child_safety" in detail, "the report's category is shown")
    check("pending" in detail, "the report's status is shown")
    check("No actions taken yet" in detail or "dismiss" in detail,
          "the action history is shown")
    check("evidence" in detail.lower(), "evidence state is shown")

    # 4b. The queue's "open" button and the action's submit button are readable
    # in every state that has text: at rest, under the pointer, and enabled.
    # ed72c66 moved all three to var(--accent) at 4.47:1 or worse (the resting
    # label, accent on this dark page, was 3.76:1); production had 7.90:1.
    # :disabled is exempt under WCAG 1.4.3 (inactive components).
    rest = b.js("(function(){" + _CONTRAST_JS + "var e=document.querySelector("
                "'.mod-open');return e?contrastOf(e):null;})()")
    check(bool(rest) and rest["ratio"] >= 4.5,
          "the queue's open button meets 4.5:1 at rest",
          f"{rest and rest['ratio']}:1, {rest and rest['fg']} on {rest and rest['bg']}")
    hov = _hovered_contrast(b, ".mod-open")
    check(bool(hov) and hov.get("hovered") and hov["ratio"] >= 4.5,
          "and under the pointer",
          f"hovered={hov and hov.get('hovered')} {hov and hov['ratio']}:1 on "
          f"{hov and hov['bg']}")
    sub = b.js("(function(){" + _CONTRAST_JS + "var e=document.querySelector("
               "'.mod-submit');return e?{c:contrastOf(e),dis:e.disabled}:null;})()")
    check(bool(sub) and not sub["dis"] and sub["c"]["ratio"] >= 4.5,
          "the action's submit button meets 4.5:1 while enabled",
          f"{sub and sub['c']['ratio']}:1 on {sub and sub['c']['bg']}, "
          f"disabled={sub and sub['dis']}")

    # 5. Choose a disposition and write a note.
    b.js("document.getElementById('mod-action-select').value = 'dismiss';"
         "document.getElementById('mod-action-note').value = "
         "'uicheck synthetic — local only';")

    # 6. Arm, then CANCEL. Nothing must be submitted.
    b.js("submitModerationAction(" + json.dumps(pid) + ")")
    time.sleep(0.4)
    armed = str(b.js("document.getElementById('mod-feedback').textContent"))
    check("Confirm" in armed, "arming shows a confirmation step", armed[:120])
    b.js("cancelModerationAction()")
    time.sleep(0.4)
    with app.app_context():
        from app import Report as R2
        still = R2.query.filter_by(public_id=pid).first()
        check(still.status == "pending",
              "cancelling the confirmation does NOT submit an action",
              f"status became {still.status}")

    # 7 & 8. Arm again, confirm, and read what the page then shows.
    b.js("submitModerationAction(" + json.dumps(pid) + ")")
    time.sleep(0.4)
    b.js("submitModerationAction(" + json.dumps(pid) + ")")
    time.sleep(2.0)
    after = str(b.js("document.getElementById('moderation-detail').textContent"))
    check("closed" in after, "the page shows the report as closed after the action",
          after[:200])
    check("dismissed" in after, "the page shows the disposition it applied")
    check("uicheck synthetic" in after,
          "the audit note appears in the action history the page re-read")

    with app.app_context():
        from app import Report as R3
        final = R3.query.filter_by(public_id=pid).first()
        check(final.status == "closed" and final.disposition == "dismissed",
              "the database agrees with what the page displayed",
              f"db: status={final.status} disposition={final.disposition}")

    # 9. Filters.
    for status in ("closed", "all"):
        b.js(f"loadModerationQueue(null, {json.dumps(status)})")
        time.sleep(1.4)
        t = str(b.js("document.getElementById('moderation-queue').textContent"))
        check(pid[:12] in t, f"the '{status}' filter lists the closed report",
              t[:160])

    # 10. Usable at phone width: controls visible, tappable, no sideways scroll.
    b.js("loadModerationQueue(null, 'pending')")
    time.sleep(1.2)
    metrics = b.js(
        "(function(){var s=document.documentElement.scrollWidth,"
        "i=window.innerWidth,"
        "f=document.querySelector('.mod-filter'),"
        "r=f?f.getBoundingClientRect():null;"
        "return {scroll:s, inner:i, h:r?r.height:0, vis:!!(r&&r.width>0)};})()")
    check(bool(metrics and metrics.get("vis")),
          "the queue filters are visible at phone width")
    check(bool(metrics and metrics.get("h", 0) >= 40),
          "queue controls are a usable size at phone width",
          f"height was {metrics.get('h') if metrics else '?'}px")
    check(bool(metrics and metrics["scroll"] <= metrics["inner"] + 2),
          "the moderation section does not force horizontal page scroll",
          f"scrollWidth {metrics.get('scroll')} vs innerWidth {metrics.get('inner')}")

    # 11. Every filter chip, selected or not, is readable: WCAG AA 4.5:1.
    #
    # Measured from the RENDERED colours, not the stylesheet. ed72c66 swapped
    # the selected chip's hardcoded #4338ca for var(--accent) in a commit whose
    # purpose was contrast, and white on #6366f1 is 4.47:1 -- 0.03 short, found
    # by an audit rather than by any check. 13.6px bold is not large text (that
    # starts at 18.66px bold), so 4.5:1 is the applicable threshold.
    chips = b.js(
        "(function(){" + _CONTRAST_JS + "return Array.prototype.map.call("
        "document.querySelectorAll('.mod-filter'),function(f){var c=contrastOf(f);"
        "return {status:f.dataset.status, active:f.classList.contains('active'),"
        "ratio:c.ratio, bg:c.bg};});})()") or []
    check(any(c.get("active") for c in chips),
          "a filter chip is shown as selected", str(chips)[:160])
    weak = [c for c in chips if c.get("ratio", 0) < 4.5]
    check(bool(chips) and not weak,
          "every queue filter chip meets 4.5:1 contrast, the selected one included",
          "; ".join(f"{c['status']}{' (selected)' if c['active'] else ''} "
                    f"{c['ratio']}:1 on {c['bg']}" for c in weak))


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

    # Preflight: a token minted HERE must be one the server THERE accepts.
    # If the running server booted with different secrets than this process
    # loaded, every authenticated check fails with a confusing downstream
    # KeyError instead of naming the cause. Find that out in one call.
    try:
        _u, _tok = make_user(flask_app, "preflight")
        _req = urllib.request.Request(
            base + "/api/daily", headers={"Authorization": f"Bearer {_tok}"})
        with urllib.request.urlopen(_req, timeout=20) as _r:
            _ok = _r.status == 200
    except urllib.error.HTTPError as exc:
        print(f"the server at {base} rejected a token minted by this process "
              f"(HTTP {exc.code}).")
        print("The two are not using the same JWT_SECRET_KEY / database. Start "
              "the server the same way this script reads its config — from "
              ".env, e.g. `make run` — rather than with inline overrides.")
        return 2
    except Exception as exc:
        print(f"preflight call to {base}/api/daily failed ({exc}).")
        return 2
    if not _ok:
        print(f"preflight call to {base}/api/daily did not return 200.")
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
        check_guest_promise_is_true(browser, base)
        check_a_completion_that_did_not_save_says_so(browser, base, flask_app)
        check_celebration_stays_clear_of_the_nav(browser, base, flask_app)
        check_team_witness(browser, base, flask_app)
        check_photo_sharing(browser, base, flask_app)
        check_side_quests_still_work(browser, base, flask_app)
        check_moderation_operator_can_close_a_report(browser, base, flask_app)
        check_step_up_is_offered_not_imposed(browser, base, flask_app)
        check_panes_and_solo_first(browser, base, flask_app)
        check_brain_boost_can_be_answered(browser, base, flask_app)
        check_someone_can_actually_sign_up(browser, base, flask_app)
        check_coming_back_after_a_while(browser, base, flask_app)
        check_discovery_types_reach_a_reader(browser, base, flask_app)
        check_acorns_are_spendable_without_a_team(browser, base, flask_app)
        check_display_name_can_be_set_changed_and_cleared(browser, base, flask_app)
        check_rickie_roams(browser, base, flask_app)
        check_rickie_is_never_left_on_content(browser, base, flask_app)
        check_appeals_are_reachable(browser, base, flask_app)
        check_appeals_stay_hidden_for_everybody_else(browser, base, flask_app)
        check_accessibility_basics(browser, base, flask_app)
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
