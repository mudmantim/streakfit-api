#!/usr/bin/env python3
"""Does prompt caching actually pay at StreakFit's traffic? Measure, don't assume.

Caching the frozen personality prompt looks like an obvious win — it is ~63% of
every request and byte-identical every time. It is not obviously a win, because
a cache entry lives about five minutes and a WRITE costs ~1.25x input. At low
traffic most requests arrive cold, pay the write premium, and cost MORE.

So this sends real requests at realistic spacings and reads the numbers the API
returns — `cache_creation_input_tokens` (a write) and `cache_read_input_tokens`
(a hit) — rather than reasoning about what ought to happen.

PATTERNS, chosen to bracket how this app is actually used:

  burst      three messages a few seconds apart. One conversation. This is the
             best case and the only one caching can win outright.
  spaced     two messages ~90s apart — a person thinking between replies. Still
             inside the TTL.
  cold       two messages either side of the TTL. This is the shape of a
             low-traffic app: one user in the morning, another at night.

Costs real money. Run it behind scripts/eval_spend_guard.py like any paid
evaluation, and pass --estimate first.

    python scripts/coach_cache_probe.py --estimate
    python scripts/coach_cache_probe.py --base-url http://localhost:5057
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

PRICE_IN, PRICE_OUT = 2.00, 10.00
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.10

# (name, [gaps in seconds before each message])
PATTERNS = [
    ("burst", [0, 8, 8], "three messages seconds apart — one conversation"),
    ("spaced", [0, 90], "~90s apart — thinking between replies, inside the TTL"),
    ("cold", [0, 330], "either side of the ~5 min TTL — the low-traffic shape"),
]

MESSAGES = [
    "hey Rickie",
    "what should I do today?",
    "is walking actually exercise?",
    "how do streaks work again?",
]


def _meter_rows(state_path):
    """Per-call usage the spend guard recorded, newest last."""
    p = Path(state_path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("per_call", [])
    except Exception:
        return []


def post(base, token, message):
    req = urllib.request.Request(
        f"{base}/api/coach",
        data=json.dumps({"message": message, "context": {"type": "general"}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def cost_of(u):
    """Dollars for one reply, from the usage the API reported."""
    fresh = getattr(u, "input_tokens", 0) or 0
    write = getattr(u, "cache_creation_input_tokens", 0) or 0
    read = getattr(u, "cache_read_input_tokens", 0) or 0
    out = getattr(u, "output_tokens", 0) or 0
    return (fresh / 1e6 * PRICE_IN
            + write / 1e6 * PRICE_IN * CACHE_WRITE_MULT
            + read / 1e6 * PRICE_IN * CACHE_READ_MULT
            + out / 1e6 * PRICE_OUT)


def estimate():
    calls = sum(len(g) for _n, g in ((p[0], p[1]) for p in PATTERNS))
    secs = sum(sum(g) for _n, g in ((p[0], p[1]) for p in PATTERNS))
    print(f"{len(PATTERNS)} patterns, {calls} paid replies")
    print(f"estimated cost : ~${calls * 0.012:.2f} (at ~$0.012/reply uncached)")
    print(f"estimated time : ~{secs / 60:.0f} min of deliberate waiting, "
          f"plus request time")
    print("\nRun it TWICE — once with STREAKFIT_COACH_CACHE=1 on the server and")
    print("once without — to get the comparison. This script reads whatever the")
    print("server is configured to do; it cannot turn caching on by itself.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:5057")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--label", default="run")
    ap.add_argument("--out", default=str(ROOT / "coach_cache_probe.json"))
    ap.add_argument("--state", default="",
                    help="the spend guard's --state file; usage is read from it")
    a = ap.parse_args()
    if a.estimate:
        return estimate()

    base = a.base_url.rstrip("/")
    if (urlparse(base).hostname or "") not in LOCAL_HOSTS:
        print("local-only: this mints accounts directly in the database.")
        return 2

    import os
    os.environ.setdefault("SECRET_KEY", "eval")
    os.environ.setdefault("JWT_SECRET_KEY", "eval")
    from coach_eval import make_accounts
    from app import app as flask_app

    tokens = make_accounts(flask_app, len(PATTERNS))
    rows = []
    print(f"cache probe [{a.label}] — {len(PATTERNS)} patterns\n")

    for i, (name, gaps, why) in enumerate(PATTERNS):
        _u, token = tokens[i]
        print(f"{name}: {why}")
        for n, gap in enumerate(gaps):
            if gap:
                print(f"   waiting {gap}s ...", flush=True)
                time.sleep(gap)
            before = len(_meter_rows(a.state))
            status, data = post(base, token, MESSAGES[n % len(MESSAGES)])
            if status != 200:
                print(f"   !! HTTP {status} {str(data)[:120]}")
                continue
            # Usage comes from the spend guard's own record, not the response:
            # /api/coach is a product endpoint and has no business returning
            # telemetry to a client just because a harness wants it.
            rowsnow = _meter_rows(a.state)
            if len(rowsnow) <= before:
                print("   !! the guard recorded no call — is it in front of this port?")
                continue
            usage = rowsnow[-1]
            w = usage.get("cache_creation_input_tokens") or 0
            r = usage.get("cache_read_input_tokens") or 0
            f = usage.get("input_tokens") or 0
            o = usage.get("output_tokens") or 0
            cost = (f / 1e6 * PRICE_IN + w / 1e6 * PRICE_IN * CACHE_WRITE_MULT
                    + r / 1e6 * PRICE_IN * CACHE_READ_MULT + o / 1e6 * PRICE_OUT)
            kind = "WRITE" if w else ("HIT" if r else "plain")
            print(f"   msg {n + 1}: {kind:5s} fresh={f:5d} write={w:5d} read={r:5d} "
                  f"out={o:4d}  ${cost:.5f}")
            rows.append({"pattern": name, "n": n + 1, "kind": kind, "fresh": f,
                         "write": w, "read": r, "out": o, "cost": round(cost, 6),
                         "label": a.label})
        print()

    total = sum(x["cost"] for x in rows)
    hits = sum(1 for x in rows if x["kind"] == "HIT")
    print("=" * 62)
    print(f"{len(rows)} replies, {hits} cache hits, total ${total:.5f}, "
          f"mean ${total / max(1, len(rows)):.5f}/reply")
    out = Path(a.out)
    prior = json.loads(out.read_text()) if out.exists() else []
    out.write_text(json.dumps(prior + rows, indent=2))
    print(f"appended to {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
