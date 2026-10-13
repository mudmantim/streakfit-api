#!/usr/bin/env python3
"""Watch a STAGING deployment second by second: which build answers, and
whether sign-in, a live session and a revoked session behave.

    STAGING_PROBE_PASSWORD=... python -I scripts/staging_probe.py <staging base url> \\
        --username <synthetic staging account> [--revoked-token-file <path>] \\
        [--seconds 600] [--interval 1.0] > probe.csv

Prints one CSV line per round:
    utc, build_sha, identity_status, health_status, login_status,
    live_session_status, revoked_session_status, revoked_error

Read it afterwards for the questions the staging plan asks: did the SHA ever
change to the failed build, was there any gap (status 0 = no answer), any 5xx,
did the revoked session ever get anything but 401 session_ended.

SAFETY: refuses any URL whose host is streakfit.pro (production) or not
https, and takes the password from the environment only, never argv. Use a
synthetic staging account; it only signs in and reads /api/me.
docs/operations/staging-pw1-test-plan.md.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PRODUCTION_HOSTS = {'streakfit.pro', 'www.streakfit.pro'}


def call(base, path, method='GET', body=None, token=None, timeout=10):
    req = urllib.request.Request(base + path, method=method,
                                 headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'null')
        except ValueError:
            return e.code, None
    except Exception:
        return 0, None                       # no answer at all: a gap


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('base_url')
    p.add_argument('--username', required=True)
    p.add_argument('--revoked-token-file')
    p.add_argument('--seconds', type=float, default=600)
    p.add_argument('--interval', type=float, default=1.0)
    a = p.parse_args(argv)
    url = urllib.parse.urlparse(a.base_url)
    if url.scheme != 'https' or (url.hostname or '').lower() in PRODUCTION_HOSTS:
        print('refusing: staging only (https, and never streakfit.pro)', file=sys.stderr)
        return 2
    password = os.environ.get('STAGING_PROBE_PASSWORD')
    if not password:
        print('set STAGING_PROBE_PASSWORD in the environment (never on the command line)', file=sys.stderr)
        return 2
    base = a.base_url.rstrip('/')
    revoked = open(a.revoked_token_file).read().strip() if a.revoked_token_file else None
    live = None
    print('utc,build_sha,identity_status,health_status,login_status,'
          'live_session_status,revoked_session_status,revoked_error', flush=True)
    end = time.monotonic() + a.seconds
    rounds = 0
    while time.monotonic() < end:
        started = time.monotonic()
        ident_status, ident = call(base, '/api/build-identity')
        sha = ((ident or {}).get('gitSha') or '') if isinstance(ident, dict) else ''
        health_status, _ = call(base, '/health')
        # A real sign-in every 15th round (/api/login allows 10 a minute, and
        # a 429 would hide what this is for); '' on the rounds in between.
        login_status = ''
        if rounds % 15 == 0:
            login_status, login = call(base, '/api/login', 'POST', {'username': a.username, 'password': password})
            if login_status == 200 and isinstance(login, dict):
                live = login.get('access_token') or live
        rounds += 1
        live_status = call(base, '/api/me', token=live)[0] if live else ''
        rev_status, rev_err = ('', '')
        if revoked:
            rev_status, body = call(base, '/api/me', token=revoked)
            rev_err = (body or {}).get('error', '') if isinstance(body, dict) else ''
        print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')},{sha},{ident_status},"
              f"{health_status},{login_status},{live_status},{rev_status},{rev_err}", flush=True)
        time.sleep(max(0.0, a.interval - (time.monotonic() - started)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
