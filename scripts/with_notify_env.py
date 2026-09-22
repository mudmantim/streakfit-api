#!/usr/bin/env python3
"""Run a command with notification secrets loaded from a file, printing none.

WHY A FILE, AND WHY THIS WRAPPER

The API key must not be a command-line argument (arguments are visible in `ps`
to every process on the box), must not be a shell-history entry (this machine
has HISTCONTROL=ignoredups, so the leading-space trick does nothing), and must
not appear in a transcript, a log or a commit. So it lives in one file that
only its owner can read, and reaches the process through the environment.

It is PARSED, not sourced. `set -a; . file` hands the contents to the shell,
so a stray backtick or $(...) in a pasted value would execute. This reads
KEY=VALUE lines as data and never evaluates them.

It prints which names it loaded and never a value, not even a prefix.

USAGE
    python scripts/with_notify_env.py -- python scripts/notification_preflight.py
    python scripts/with_notify_env.py --file ~/.streakfit-notify.env -- <cmd>
"""
import argparse
import os
import stat
import sys

DEFAULT_FILE = os.path.expanduser('~/.streakfit-notify.env')
# Only these may be set from the file. An env file is a convenient place for a
# typo to become a silent override of something unrelated, so the set of names
# it is allowed to touch is closed rather than "whatever is in there".
ALLOWED = {
    'RESEND_API_KEY',
    'STREAKFIT_NOTIFY_CHANNEL',
    'STREAKFIT_NOTIFY_FROM',
    'STREAKFIT_NOTIFY_TO',
    'STREAKFIT_PUBLIC_URL',
    # ADMIN_SECRET is deliberately NOT here. The operator credential for
    # /api/admin/* lives in its own file (~/.streakfit-admin.env) and is loaded
    # by scripts/moderation_report.py alone. Two credentials of different blast
    # radius should not be handed to a process together: anything that needs to
    # send an alert has no business being able to dismiss a child-safety
    # report, and a leak of one file should not be a leak of both.
}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--file', default=DEFAULT_FILE)
    ap.add_argument('command', nargs=argparse.REMAINDER)
    args = ap.parse_args()

    cmd = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not cmd:
        print("\n  Nothing to run. Put the command after `--`.\n", file=sys.stderr)
        return 2

    path = os.path.expanduser(args.file)
    if not os.path.exists(path):
        print(f"\n  No secrets file at {path}\n", file=sys.stderr)
        print("  Create it (the key is never echoed, never an argument, and the\n"
              "  `read` makes it input to a command rather than a history entry):\n",
              file=sys.stderr)
        print("    umask 077 && read -rsp 'Paste Resend API key (hidden): ' K \\\n"
              "      && printf 'RESEND_API_KEY=%s\\n' \"$K\" > ~/.streakfit-notify.env \\\n"
              "      && unset K && echo && ls -l ~/.streakfit-notify.env\n",
              file=sys.stderr)
        return 1

    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        print(f"\n  REFUSING: {path} is mode {mode:03o} — readable by others.\n"
              f"  Fix it first:  chmod 600 {path}\n", file=sys.stderr)
        return 1

    loaded, skipped = [], []
    with open(path, 'r', encoding='utf-8') as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            name, _, value = line.partition('=')
            name = name.strip()
            # Tolerate quotes a person might add; take the value as literal text.
            value = value.strip().strip('"').strip("'")
            if name not in ALLOWED:
                skipped.append(name)
                continue
            os.environ[name] = value
            loaded.append(name)

    if not loaded:
        print(f"\n  {path} set none of the expected variables.\n", file=sys.stderr)
        return 1

    print(f"  loaded from {path}: {', '.join(sorted(loaded))}", file=sys.stderr)
    if skipped:
        print(f"  ignored (not in the allowed set): {', '.join(sorted(set(skipped)))}",
              file=sys.stderr)
    print("  values are not displayed.\n", file=sys.stderr)

    os.execvp(cmd[0], cmd)


if __name__ == '__main__':
    sys.exit(main())
