#!/usr/bin/env python3
"""Watch a STAGING deployment second by second: which build answers, and
whether sign-in, a live session and a revoked session behave.

    STAGING_PROBE_PASSWORD=... python -I scripts/staging_probe.py <staging base url> \\
        --username <synthetic staging account> [--revoked-token-file <path>] \\
        [--baseline-sha <sha serving before the deploy>] \\
        [--seconds 600] [--interval 1.0] > probe.csv

    STAGING_PROBE_PASSWORD=... python -I scripts/staging_probe.py <staging base url> \\
        --username <synthetic staging account> --issue-token-file <new path>

Prints one CSV line per round:
    utc, build_sha, identity_status, health_status, login_status,
    live_session_status, revoked_session_status, revoked_error

Read it afterwards for the questions the staging plan asks: did the SHA ever
change to the failed build, was there any gap (status 0 = no answer), any 5xx,
did the revoked session ever get anything but 401 session_ended. A summary
goes to stderr at the end.

--baseline-sha makes the run a pass/fail check for a deploy that must NOT
take over (staging S3): it refuses to start unless that build is the one
answering, and exits 1 if any round saw another build, a gap or a 5xx.

--issue-token-file signs in once and writes the session token to a NEW file
that only the owner can read (0600, never overwritten), then exits: the
"other device" whose session a password change should end. The run mode
refuses a token file anyone else can read. Delete the file after testing.

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


def write_token_file(path, token):
    """Create PATH owner-only and write TOKEN; never overwrite an existing file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token)


def read_token_file(path):
    """The token, or None if the file is readable by anyone but its owner."""
    st = os.stat(path)
    if st.st_mode & 0o077 or st.st_uid != os.getuid():
        return None
    with open(path) as f:
        return f.read().strip()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('base_url')
    p.add_argument('--username', required=True)
    p.add_argument('--revoked-token-file')
    p.add_argument('--issue-token-file')
    p.add_argument('--baseline-sha')
    p.add_argument('--seconds', type=float, default=600)
    p.add_argument('--interval', type=float, default=1.0)
    p.add_argument('--rounds', type=int, help='stop after this many rounds (default: --seconds decides)')
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
    if a.issue_token_file:
        status, login = call(base, '/api/login', 'POST', {'username': a.username, 'password': password})
        token = login.get('access_token') if status == 200 and isinstance(login, dict) else None
        if not token:
            print(f'sign-in failed ({status}); no file written', file=sys.stderr)
            return 1
        try:
            write_token_file(a.issue_token_file, token)
        except FileExistsError:
            print('refusing: the token file already exists', file=sys.stderr)
            return 2
        print('token written (owner-only); delete it after testing', file=sys.stderr)
        return 0
    revoked = None
    if a.revoked_token_file:
        revoked = read_token_file(a.revoked_token_file)
        if revoked is None:
            print('refusing: the token file is readable by others (chmod 600)', file=sys.stderr)
            return 2
    baseline = (a.baseline_sha or '').strip().lower() or None
    if baseline:
        sha = _identity_sha(call(base, '/api/build-identity'))
        if sha.lower() != baseline:
            print(f'refusing: the baseline build is not live (serving {sha or "nothing"!r})', file=sys.stderr)
            return 3
    seen = {'rounds': 0, 'gaps': 0, '5xx': 0, 'other_sha': 0, 'revoked_not_ended': 0}
    live = None
    print('utc,build_sha,identity_status,health_status,login_status,'
          'live_session_status,revoked_session_status,revoked_error', flush=True)
    end = time.monotonic() + a.seconds
    rounds = 0
    while time.monotonic() < end and (a.rounds is None or rounds < a.rounds):
        started = time.monotonic()
        ident_status, ident = call(base, '/api/build-identity')
        sha = _identity_sha((ident_status, ident))
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
        statuses = [s for s in (ident_status, health_status, login_status, live_status, rev_status) if s != '']
        seen['rounds'] += 1
        seen['gaps'] += 0 in statuses
        seen['5xx'] += any(s >= 500 for s in statuses)
        seen['other_sha'] += bool(baseline) and sha.lower() != baseline
        seen['revoked_not_ended'] += bool(revoked) and (rev_status, rev_err) != (401, 'session_ended')
        time.sleep(max(0.0, a.interval - (time.monotonic() - started)))
    print('summary: ' + ', '.join(f'{k}={v}' for k, v in seen.items()), file=sys.stderr)
    if baseline and (seen['gaps'] or seen['5xx'] or seen['other_sha'] or seen['revoked_not_ended']):
        print('FAIL: the baseline build did not answer cleanly throughout', file=sys.stderr)
        return 1
    return 0


def _identity_sha(result):
    _, ident = result
    return ((ident or {}).get('gitSha') or '') if isinstance(ident, dict) else ''


if __name__ == '__main__':
    sys.exit(main())
