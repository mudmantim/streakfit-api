#!/usr/bin/env python3
"""StreakFit — post-deploy verification protocol.

Runs the checks that should follow every production deploy, in one pass, with
one exit code. `scripts/verify_all.py` proves the API contract holds; this proves
*this deploy* is live, healthy, and serving the intended build, and it exercises
the paths that only matter against real production (coach with a real key, rate
limiting under real config, sustained health over a watch window).

Standard library only, same as the verification suite.

Usage:
    python scripts/post_deploy_check.py                        # against streakfit.pro
    python scripts/post_deploy_check.py --expect-sw v0748      # also assert the deployed SW version
    python scripts/post_deploy_check.py --watch-minutes 20     # sustained health watch
    python scripts/post_deploy_check.py --base-url http://localhost:5000 --skip-suite

Exit 0 = deploy verified. 1 = at least one check failed.

Accounts: reuses the verification suite's convention — only ever creates
throwaway `qa_smoke_*` accounts. Anything it cannot clean up itself is reported
explicitly at the end rather than left silent, because a team owner cannot be
deleted through any API route (see scripts/cleanup_qa_smoke.py).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verification._client import ApiClient  # noqa: E402  (path setup must precede)

results: list[tuple[bool, str, str]] = []   # (passed, name, detail)
warnings: list[str] = []
residue: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    results.append((bool(passed), name, detail))
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return bool(passed)


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"  WARN  {msg}")


def fetch_text(url: str, timeout: int = 20):
    """Return (status, text, headers) without raising on HTTP error codes."""
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})
    except urllib.error.URLError as e:
        return 0, f"network error: {e}", {}


# ── 1. Health and identity of the deployed build ─────────────────────────────
def check_health(base: str, expect_sw: str | None) -> None:
    print("\n[Health & build identity]")
    t0 = time.time()
    status, body, _ = fetch_text(f"{base}/health")
    check("health returns 200", status == 200, f"HTTP {status} in {time.time() - t0:.2f}s")
    try:
        check("health body is {'status': 'ok'}", json.loads(body).get("status") == "ok", body.strip()[:60])
    except json.JSONDecodeError:
        check("health body is JSON", False, body.strip()[:60])

    status, body, _ = fetch_text(f"{base}/static/sw.js")
    check("service worker is served", status == 200, f"HTTP {status}")
    sw_version = ""
    for line in body.splitlines()[:5]:
        if "CACHE" in line and "=" in line:
            sw_version = line.split("'")[1] if "'" in line else line.strip()
            break
    if expect_sw:
        # This is the deploy-went-live signal: Render is git-triggered, so a
        # flipped cache version is proof the new build is the one serving.
        check(
            f"deployed service worker carries {expect_sw}",
            expect_sw in sw_version,
            f"serving {sw_version!r}",
        )
    else:
        print(f"  INFO  deployed service worker: {sw_version!r}")


# ── 2. Security headers survived the deploy ──────────────────────────────────
def check_security_headers(base: str) -> None:
    print("\n[Security headers]")
    expected = {
        "x-frame-options": "DENY",
        "content-security-policy": "frame-ancestors 'none'",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
        "strict-transport-security": "max-age=",
    }
    status, _, headers = fetch_text(base + "/")
    lower = {k.lower(): v for k, v in headers.items()}
    check("index returns 200", status == 200, f"HTTP {status}")
    for header, needle in expected.items():
        got = lower.get(header, "")
        check(f"{header} present", needle.lower() in got.lower(), got[:60] or "(absent)")


# ── 3. Static assets a first paint depends on ────────────────────────────────
def check_static_assets(base: str) -> None:
    print("\n[Static assets]")
    for path in (
        "/static/app.js", "/static/style.css", "/static/manifest.json",
        "/static/rickie.svg", "/static/icons/icon-192.png",
        "/static/exercises/arm_circles.svg",
    ):
        status, _, _ = fetch_text(base + path)
        check(f"{path} serves", status == 200, f"HTTP {status}")


# ── 4. Auth: register, login, and a rejected bad password ────────────────────
def check_auth(api: ApiClient, tag: str) -> tuple[str | None, str, str]:
    print("\n[Auth]")
    username = f"qa_smoke_pd_{tag}"
    password = "qa-post-deploy-" + secrets.token_hex(6)

    # /api/register is capped at 5/minute and the limiter bucket is shared by
    # everything hitting this deploy. A 429 here means the window is busy, not
    # that registration is broken, so wait the window out and try once more.
    status, body = api.request("POST", "/api/register", body={"username": username, "password": password})
    if status == 429:
        warn("register hit the 5/min rate limit — waiting 65s for the window to clear")
        time.sleep(65)
        status, body = api.request("POST", "/api/register", body={"username": username, "password": password})
    if not check("register a throwaway account", status == 201, f"HTTP {status}"):
        return None, username, password
    residue.append(f"account {username} (delete via scripts/cleanup_qa_smoke.py)")

    status, body = api.request("POST", "/api/login", body={"username": username, "password": password})
    token = body.get("access_token") or body.get("token") if isinstance(body, dict) else None
    check("login returns a token", status == 200 and bool(token), f"HTTP {status}")

    status, _ = api.request("POST", "/api/login", body={"username": username, "password": "wrong-" + password})
    check("wrong password is rejected", status == 401, f"HTTP {status}")

    status, _ = api.request("GET", "/api/daily")
    check("unauthenticated /api/daily is rejected", status in (401, 422), f"HTTP {status}")
    return token, username, password


# ── 5. The primary flow: Daily Mission from empty to complete ────────────────
def check_daily_mission(api: ApiClient, token: str) -> None:
    print("\n[Daily Mission — the primary flow]")
    status, body = api.request("GET", "/api/daily", token=token)
    if not check("fetch today's mission", status == 200, f"HTTP {status}"):
        return
    exercises = body.get("exercises", [])
    check("mission has 5 exercises", len(exercises) == 5, f"got {len(exercises)}")
    check(
        "every exercise promises an illustration path",
        all(e.get("image_url") for e in exercises),
        f"{sum(1 for e in exercises if e.get('image_url'))}/{len(exercises)}",
    )
    # The illustrations are referenced from server data, so a rename would only
    # surface as a broken image in a real browser. Fetch them for real.
    broken = []
    for e in exercises:
        st, _, _ = fetch_text(api.base_url + e["image_url"])
        if st != 200:
            broken.append(f"{e['image_url']} -> HTTP {st}")
    check("all 5 illustrations actually load", not broken, "; ".join(broken) or "5/5")

    completed = 0
    for e in exercises:
        status, body = api.request("POST", f"/api/daily/{e['key']}/complete", token=token)
        if status == 200:
            completed += 1
        else:
            check(f"complete {e['key']}", False, f"HTTP {status} {str(body)[:80]}")
    check("all 5 exercises complete", completed == 5, f"{completed}/5")

    status, body = api.request("GET", "/api/daily", token=token)
    done = sum(1 for e in body.get("exercises", []) if e.get("completed"))
    check("mission reads back as 5/5", done == 5, f"{done}/5")
    # Deliberately NOT asserted here: `insight` is present before completion too
    # (it is shown as a discoverable teaser by design, see PROJECT_JOURNAL v0746),
    # so its presence proves nothing about completion. The counters below do.
    check("completed_count reports 5", body.get("completed_count") == 5, str(body.get("completed_count")))

    status, body = api.request("GET", "/api/me", token=token)
    if check("fetch user stats", status == 200, f"HTTP {status}"):
        check("streak is day 1 after the first mission", body.get("current_streak") == 1,
              f"current_streak={body.get('current_streak')}")
        check("total_missions is 1", body.get("total_missions") == 1,
              f"total_missions={body.get('total_missions')}")


# ── 6. One real coach interaction ────────────────────────────────────────────
def check_coach(api: ApiClient, token: str) -> None:
    print("\n[Rickie coach — one real interaction]")
    status, body = api.request(
        "POST", "/api/coach", token=token,
        body={"message": "Just finished my first five. How did I do?",
              "context": {"type": "general"}},
    )
    if status == 503:
        # Documented graceful degradation: no API key configured.
        warn("coach returned 503 — no ANTHROPIC_API_KEY in this environment (graceful, in-character)")
        check("coach degrades gracefully rather than erroring", True, "HTTP 503 as designed")
        return
    if not check("coach returns 200", status == 200, f"HTTP {status} {str(body)[:100]}"):
        return
    reply = (body or {}).get("reply", "")
    check("coach reply is non-empty", bool(reply.strip()), f"{len(reply)} chars")
    check("coach reply is not an error string", "error" not in reply.lower()[:40], reply[:70])


# ── 7. Rate limits are actually enforced, per client ─────────────────────────
REGISTER_LIMIT_PER_MIN = 5          # must match @limiter.limit on /api/register


def check_rate_limiting(api: ApiClient, base: str, cooldown: float = 65) -> None:
    """Prove the configured per-IP limit is the limit a real client actually gets.

    This is the regression gate for the ProxyFix(x_for=2) fix. Before it, the
    limiter keyed on `request.remote_addr` — gunicorn's TCP peer, a
    Render-internal address — so requests fanned across ~6-7 buckets and
    /api/register admitted ~35/min instead of 5: 12 POSTs over one keep-alive
    connection hit the limit exactly, while 50 fresh connections allowed 30 and
    60 allowed 37.

    Probes /api/register with a payload rejected at validation, so nothing
    touches the database and no account is created.

    The assertion is two-sided on purpose, because BOTH directions are bugs:

      allowed > 5  -> the key is too granular (partitioned across proxy hops or
                      worker processes); limits are looser than configured.
      allowed = 0  -> this client's allowance was already gone before we started,
                      which on a quiet deploy points at a key too coarse to be
                      per-client (e.g. one bucket shared by every user). This is
                      a smoke signal, not a proof of granularity: a single prober
                      against an idle service cannot distinguish a correct key
                      from a shared one. Granularity is proven by the ProxyFix
                      unit tests, which pin that hop -2 is what gets selected.

    It also catches the one real fragility of a hop count: if Render changes its
    topology, or a customer-owned Cloudflare zone is ever put in front and adds
    a third hop, x_for=2 silently resolves to the wrong entry — and this fails
    on the next deploy instead of going unnoticed.

    Note on how this was validated: the assertions and their mechanics were
    exercised against a local server, but the loose-key failure cannot be
    reproduced locally — a single machine presents one bucket either way. The
    evidence that this gate discriminates is the production measurement taken
    before the fix, where /api/register admitted 30 of 50 and 37 of 60 requests;
    `allowed <= 5` would have failed on both.
    """
    print("\n[Rate limits enforced per client]")

    # The window is per-minute, and earlier checks in this run have already made
    # requests. Start from a clean window or the count means nothing.
    if cooldown > 0:
        print(f"  waiting {cooldown:g}s for a clean rate-limit window...")
        time.sleep(cooldown)

    probes = REGISTER_LIMIT_PER_MIN * 2      # enough to cross the limit, cheap
    allowed, limited, codes = 0, 0, []
    for _ in range(probes):
        status, _ = api.request(
            "POST", "/api/register",
            body={"username": "x", "password": "1"},   # rejected before any DB access
        )
        codes.append(status)
        if status == 400:
            allowed += 1
        elif status == 429:
            limited += 1

    detail = f"{allowed} allowed, {limited} limited in {probes} calls; codes={codes}"

    check("rate limiting engages at all", limited > 0, detail)
    check(
        f"limit is not looser than the configured {REGISTER_LIMIT_PER_MIN}/min",
        allowed <= REGISTER_LIMIT_PER_MIN,
        f"{allowed} allowed (expected <= {REGISTER_LIMIT_PER_MIN}) — "
        "more means the limiter key is partitioned, not per client",
    )
    check(
        "this client's own allowance was available",
        allowed >= 1,
        f"{allowed} allowed (expected >= 1) — zero on a quiet deploy suggests a "
        "key too coarse to be per-client",
    )
    unexpected = [c for c in codes if c not in (400, 429)]
    check("no unexpected status codes while probing", not unexpected, str(unexpected) or "none")


# ── 8. Sustained health watch ────────────────────────────────────────────────
def check_watch(base: str, minutes: float) -> None:
    if minutes <= 0:
        return
    print(f"\n[Sustained health watch — {minutes:g} minutes]")
    interval = 30
    polls = max(1, int(minutes * 60 / interval))
    fails, slow, times = 0, 0, []
    for i in range(polls):
        t0 = time.time()
        status, body, _ = fetch_text(f"{base}/health", timeout=20)
        dt = time.time() - t0
        times.append(dt)
        ok = status == 200 and '"ok"' in body
        if not ok:
            fails += 1
            print(f"    poll {i + 1}/{polls}: FAIL HTTP {status}")
        elif dt > 3.0:
            slow += 1
            print(f"    poll {i + 1}/{polls}: slow {dt:.2f}s")
        if i < polls - 1:
            time.sleep(interval)
    avg = sum(times) / len(times)
    check(f"health stayed green across {polls} polls", fails == 0, f"fail={fails}, avg={avg:.2f}s, max={max(times):.2f}s")
    if slow:
        warn(f"{slow}/{polls} health polls took over 3s (cold start or provider latency)")


def main() -> int:
    ap = argparse.ArgumentParser(description="StreakFit post-deploy verification")
    ap.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "https://streakfit.pro"))
    ap.add_argument("--expect-sw", default=None, help="assert the deployed sw.js cache version, e.g. v0748")
    ap.add_argument("--watch-minutes", type=float, default=0, help="sustained health watch after the checks")
    ap.add_argument("--skip-suite", action="store_true", help="skip the full verify_all suite")
    ap.add_argument("--cooldown", type=float, default=65,
                    help="seconds to wait before the suite so the 5/min register limit clears")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    tag = secrets.token_hex(3)
    api = ApiClient(base)
    started = time.time()

    print("=" * 64)
    print(f"StreakFit post-deploy verification — {base}")
    print("=" * 64)

    check_health(base, args.expect_sw)
    check_security_headers(base)
    check_static_assets(base)

    token, username, _ = check_auth(api, tag)
    if token:
        check_daily_mission(api, token)
        check_coach(api, token)
    else:
        warn("skipped mission / coach / rate-limit checks — no token")

    suite_line = "skipped"
    if not args.skip_suite:
        # The suite registers four of its own users. Combined with the one above
        # that exceeds /api/register's 5/minute cap, which made the suite fail on
        # `auth.register_a` with a 429 on the first local dry run. Waiting out the
        # window is the honest fix — raising the cap for tooling would weaken a
        # real protection.
        if args.cooldown > 0:
            print(f"\n[Cooldown {args.cooldown:g}s — letting the register rate-limit window clear]")
            time.sleep(args.cooldown)
        print("\n[Full verification suite]")
        import subprocess
        proc = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_all.py"),
             "--base-url", base],
            capture_output=True, text=True, timeout=1800, check=False,
        )
        tail = [ln for ln in proc.stdout.splitlines() if "passed" in ln and "failed" in ln]
        suite_line = tail[-1].strip() if tail else f"exit {proc.returncode}"
        check("verify_all suite passes", proc.returncode == 0, suite_line)
        if proc.returncode != 0:
            for ln in proc.stdout.splitlines():
                if "FAIL" in ln:
                    print("    " + ln.strip())
        residue.append("verify_all's own qa_smoke_* accounts and 'Smoke Test' team")

    # Runs AFTER the suite on purpose. The limiter buckets are shared, so probing
    # them first made the suite's own duplicate-username check see a 429 instead of
    # the 400 it expects — a self-inflicted failure caught on the local dry run.
    if token:
        check_rate_limiting(api, base, cooldown=args.cooldown)

    check_watch(base, args.watch_minutes)

    passed = sum(1 for ok, _, _ in results if ok)
    failed = len(results) - passed
    print("\n" + "-" * 64)
    print("Post-deploy summary")
    print("-" * 64)
    print(f"  checks      : {passed} passed, {failed} failed, {len(results)} total")
    print(f"  suite       : {suite_line}")
    print(f"  duration    : {time.time() - started:.1f}s")
    print(f"  warnings    : {len(warnings)}")
    for w in warnings:
        print(f"      - {w}")
    print("  residue created in this environment (needs cleanup):")
    for r in residue or ["(none)"]:
        print(f"      - {r}")
    print("-" * 64)
    print("VERDICT: " + ("DEPLOY VERIFIED" if failed == 0 else f"NOT VERIFIED — {failed} failing check(s)"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
