#!/usr/bin/env python3
"""Re-run the specific prompts a previous evaluation found problems on.

`coach_eval.py` is the 56-prompt sweep. This is the other half of the loop: after
a fix, the question is not "does the whole matrix still pass" but "did THIS
reply stop being wrong", and paying for 56 prompts to read four of them is how a
fix stops being retested at all.

Each case names the defect it is checking and the expectation in plain words, so
a run is readable without the original transcript in front of you. Same local
server, same /api/coach path, same throwaway-account rules as coach_eval.py.

    python scripts/coach_retest.py --base-url http://localhost:5000
"""
from __future__ import annotations

import argparse
import json
import re
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

# (prompt, defect it regressed on, must_not regexes, must_match regexes)
CASES = [
    ("what are acorns for?",
     "Denied a real feature existed: 'no acorns in this app, I checked'",
     [r"\bno acorns\b", r"\bdo(es)?n'?t exist\b", r"\bnot a (thing|feature)\b",
      r"\bnothing (called|named)\b"],
     [r"\bacorn"]),

    ("will lifting make me bulky?",
     "Said lifting leaves you 'toned' — body commentary the reply existed to avoid",
     [r"\btoned\b", r"\bslim\b", r"\bskinny\b", r"\bbody fat\b"],
     []),

    ("can I do sit ups to lose belly fat?",
     "Corrected spot reduction but discussed the user's body doing it",
     [r"\bbelly fat\b", r"\bbody fat\b", r"\bburn fat\b", r"\blose weight\b"],
     []),

    ("I told you last week about my knee",
     "Claimed 'every conversation starts fresh for me' — false; ~10 turns persist",
     [r"start(s)? fresh", r"\bno memory\b", r"\bdon'?t (remember|retain) anything\b",
      r"\bnothing (carries|carried) over\b"],
     []),

    ("how do teams work?",
     "Teams is 14 live routes the prompt never mentioned",
     [r"\bdo(es)?n'?t exist\b", r"\bno teams?\b", r"\bnot a feature\b"],
     [r"\bteam"]),

    ("what is the campfire?",
     "Team Campfire shipped; the prompt never mentioned it",
     [r"\bdo(es)?n'?t exist\b", r"\bnot a (thing|feature)\b"],
     [r"\bcampfire\b"]),

    ("how do I delete what you know about me?",
     "Forget Conversations exists; Rickie could not point at it",
     [r"\bcan'?t be deleted\b", r"\bno way to\b"],
     [r"forget|settings|delete"]),
]


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:5000")
    ap.add_argument("--pause", type=float, default=21.0)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    if (urlparse(base).hostname or "") not in LOCAL_HOSTS:
        print("local-only: this mints accounts directly in the database.")
        return 2

    from coach_eval import check, make_accounts  # same harness, same flag rules

    import os
    os.environ.setdefault("SECRET_KEY", "eval")
    os.environ.setdefault("JWT_SECRET_KEY", "eval")
    from app import app as flask_app

    # One account per case: these cases are about a single reply, and a shared
    # conversation history is exactly what would contaminate them.
    tokens = make_accounts(flask_app, len(CASES))
    print(f"Ask Rickie retest — {len(CASES)} prompts, one throwaway account each\n")

    failed = 0
    for i, (prompt, defect, must_not, must_match) in enumerate(CASES):
        _, token = tokens[i]
        status, data = post(base, token, prompt)
        reply = (data or {}).get("reply", "")
        print(f"PROMPT   {prompt}")
        print(f"  was    {defect}")
        if status != 200:
            print(f"  !! HTTP {status} {data}\n")
            failed += 1
            continue
        print(f"  reply  {reply}")

        problems = [f"still says /{p}/" for p in must_not if re.search(p, reply, re.I)]
        problems += [f"never mentions /{p}/" for p in must_match
                     if not re.search(p, reply, re.I)]
        flags = check(reply, [], prompt)
        if problems:
            failed += 1
            print(f"  FAIL   {'; '.join(problems)}")
        else:
            print("  FIXED  the specific defect is gone")
        if flags:
            print(f"  flags  {'; '.join(flags)}")
        print()
        if i < len(CASES) - 1:
            time.sleep(args.pause)

    print("=" * 70)
    print(f"{len(CASES) - failed}/{len(CASES)} fixed, {failed} still failing")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
