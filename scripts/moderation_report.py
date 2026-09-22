#!/usr/bin/env python3
"""Read or action ONE moderation report from the command line.

WHY THIS EXISTS, AND WHY ITS EXISTENCE IS A FINDING

The moderation API shipped without an operator interface. `/admin` serves a
dashboard that calls six endpoints, none of them the reports API, so a
child-safety report can be filed, generate an alert, page a human -- and leave
that human with no way to act on it. The alert even says "Open <url>/admin to
review it", which is a dead end.

So until the queue exists in the UI, this is the operator interface. It is a
stopgap and should be deleted once the real one lands.

IT NEVER PRINTS THE SECRET. The credential arrives through the environment
(see scripts/with_notify_env.py) and is sent as a header, never as an argument
-- arguments are visible in `ps` to every process on the box.

IT REFUSES TO GUESS. `--action` requires the report id to be given in full and
a note to be supplied, and it prints the report for confirmation before doing
anything unless --yes is passed.

The credential is read from ~/.streakfit-admin.env (mode 600), which is
deliberately NOT the notification credentials file.

USAGE
  python scripts/moderation_report.py --list
  python scripts/moderation_report.py --show <public_id>
  python scripts/moderation_report.py --action dismiss --note "..." <public_id>
"""
import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.request

# The operator credential lives in its OWN file, separate from the
# notification credentials.
#
# Different blast radius. A key that sends an alert email has no business
# being able to dismiss a child-safety report, and a process that needs one
# should not be handed the other. Keeping them apart also means a leak of one
# file is not a leak of both.
#
# It is PARSED, never sourced: `set -a; . file` hands the contents to a shell,
# so a stray backtick in a pasted value would execute.
ADMIN_ENV = os.path.expanduser(
    os.environ.get('STREAKFIT_ADMIN_ENV', '~/.streakfit-admin.env'))


def _load_admin_secret():
    """Return the secret, or exit with instructions. Never prints its value."""
    if os.environ.get('ADMIN_SECRET'):
        return os.environ['ADMIN_SECRET']

    if not os.path.exists(ADMIN_ENV):
        print(f"\n  No admin credential file at {ADMIN_ENV}\n", file=sys.stderr)
        print("  Create it — the secret is typed as INPUT to a running command,\n"
              "  so it is not a history entry and never an argument:\n", file=sys.stderr)
        print("    umask 077 && read -rsp 'Paste ADMIN_SECRET (hidden): ' S \\\n"
              "      && printf 'ADMIN_SECRET=%s\\n' \"$S\" > ~/.streakfit-admin.env \\\n"
              "      && unset S && echo && ls -l ~/.streakfit-admin.env\n", file=sys.stderr)
        raise SystemExit(2)

    mode = stat.S_IMODE(os.stat(ADMIN_ENV).st_mode)
    if mode & 0o077:
        print(f"\n  REFUSING: {ADMIN_ENV} is mode {mode:03o} — readable by others.\n"
              f"  Fix it first:  chmod 600 {ADMIN_ENV}\n", file=sys.stderr)
        raise SystemExit(2)

    secret = None
    with open(ADMIN_ENV, 'r', encoding='utf-8') as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, _, value = line.partition('=')
            # ONE name is honoured. An admin credential file is not a general
            # environment file, and silently setting anything else it happened
            # to contain would be a way to smuggle configuration into a
            # privileged process.
            if name.strip() == 'ADMIN_SECRET':
                secret = value.strip().strip('"').strip("'")
                break

    if not secret:
        print(f"\n  {ADMIN_ENV} contains no ADMIN_SECRET line.\n", file=sys.stderr)
        raise SystemExit(2)
    return secret

BASE = os.environ.get('STREAKFIT_PUBLIC_URL', 'https://streakfit.pro').rstrip('/')
UA = 'StreakFit-ops/1.0'

# Mirrors app.MODERATION_ACTIONS. Listed here so a typo is refused locally
# rather than by a 400 after the request has gone out.
ACTIONS = ('dismiss', 'escalate', 'restrict_reporting', 'lift_reporting_restriction',
           'restrict_content', 'unrestrict_content', 'suspend_social',
           'lift_suspension', 'remove_from_team')


def _request(path, method='GET', payload=None):
    # Sent as a HEADER, never an argument: arguments are visible in `ps` to
    # every process on the box.
    headers = {'X-Admin-Secret': _load_admin_secret(), 'Accept': 'application/json',
               'User-Agent': UA}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        raw = ''
        try:
            raw = exc.read().decode('utf-8')
        except Exception:
            pass
        body = None
        try:
            body = json.loads(raw) if raw.strip() else None
        except ValueError:
            body = None
        return exc.code, body


def _print_report(r):
    for k in ('public_id', 'category', 'status', 'created_at', 'review_due_at',
              'disposition', 'reviewed_at', 'subject_type', 'team_id'):
        if k in r:
            print(f"    {k:<16} {r[k]}")
    actions = r.get('actions') or r.get('history') or []
    if actions:
        print("    actions:")
        for a in actions:
            print(f"      - {a.get('action')} by {a.get('actor')} at {a.get('created_at')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('public_id', nargs='?')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--show', metavar='PUBLIC_ID')
    ap.add_argument('--action', choices=ACTIONS)
    ap.add_argument('--note', default='')
    ap.add_argument('--yes', action='store_true', help='skip the confirmation prompt')
    args = ap.parse_args()

    if args.list:
        status, body = _request('/api/admin/reports')
        if status != 200:
            print(f"\n  HTTP {status} listing reports.\n", file=sys.stderr)
            return 1
        rows = body if isinstance(body, list) else (body or {}).get('reports', [])
        print(f"\n  {len(rows)} report(s)\n")
        for r in rows:
            print(f"    {r.get('public_id')}  {r.get('category'):<16} "
                  f"{r.get('status'):<10} due {r.get('review_due_at')}")
        print()
        return 0

    pid = args.show or args.public_id
    if not pid:
        ap.print_help()
        return 2

    status, body = _request(f'/api/admin/reports/{pid}')
    if status == 404:
        print(f"\n  No report with id {pid}.\n", file=sys.stderr)
        return 1
    if status != 200:
        print(f"\n  HTTP {status} reading the report.\n", file=sys.stderr)
        return 1

    print("\n  REPORT\n")
    _print_report(body if isinstance(body, dict) else {})

    if not args.action:
        print()
        return 0

    if not args.note.strip():
        print("\n  --action requires --note. The note is the audit trail.\n", file=sys.stderr)
        return 2

    print(f"\n  ABOUT TO APPLY: {args.action}")
    print(f"  NOTE          : {args.note}")
    if not args.yes:
        try:
            if input("\n  Type the action name to confirm: ").strip() != args.action:
                print("\n  Cancelled. Nothing was changed.\n")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled. Nothing was changed.\n")
            return 1

    status, body = _request(f'/api/admin/reports/{pid}/action', 'POST',
                            {'action': args.action, 'note': args.note})
    if status != 200:
        print(f"\n  FAILED: HTTP {status} {body}\n", file=sys.stderr)
        return 1
    print("\n  APPLIED. Re-reading the report to confirm it actually changed:\n")
    status, after = _request(f'/api/admin/reports/{pid}')
    if status == 200 and isinstance(after, dict):
        _print_report(after)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
