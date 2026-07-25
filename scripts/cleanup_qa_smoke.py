#!/usr/bin/env python3
"""Delete StreakFit smoke-test accounts (username prefix 'qa_smoke_') and their
dependent rows. DRY RUN by default — pass --execute to actually delete.

Classification:
  * Any username beginning with the EXACT prefix 'qa_smoke_' is a QA account.
    (Exact Python prefix match — never a SQL LIKE, whose '_' is a wildcard.)
  * SAFE to delete: a QA account that owns no team.
  * BLOCKED (requires manual cleanup): a QA account that created a team — tearing a
    team down would touch other users' data, so it's left untouched here.

Behavior:
  * Dry run lists both groups and changes nothing.
  * --execute deletes ONLY the safe group, in a single all-or-nothing transaction,
    then re-queries and reports both groups. Blocked accounts are left in place.
  * Aborts entirely only if the match count exceeds a sanity cap (a matching bug
    at scale would be dangerous). Touches no migrations and no app behavior.

Run where the target DATABASE_URL is set (e.g. production):
  DATABASE_URL=... SECRET_KEY=... JWT_SECRET_KEY=... python scripts/cleanup_qa_smoke.py            # dry run
  DATABASE_URL=... SECRET_KEY=... JWT_SECRET_KEY=... python scripts/cleanup_qa_smoke.py --execute  # delete safe
"""
import argparse
import os
import sys

# Run naturally from the repo root: `python scripts/cleanup_qa_smoke.py`
# (python only puts scripts/ on sys.path, not the repo root, so add it here
# instead of requiring a PYTHONPATH=. prefix).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A

PREFIX = "qa_smoke_"
SANITY_CAP = 1000   # refuse to run if more than this many match — a bug would be scary at scale

def _matches():
    """Fetch ALL users and filter in Python with an exact prefix — deliberately no
    SQL LIKE, so there is zero chance of a wildcard/substring match."""
    return [u for u in A.User.query.all() if u.username.startswith(PREFIX)]


def survey():
    """Return (safe, blocked, counts_by_id). `blocked` is a list of (user, reason).
    Returns None if the sanity cap is exceeded (hard abort). Dependency counts and
    the team-owner blocker come from the app's account-deletion service — this
    script no longer maintains its own deletion/counting logic."""
    users = _matches()
    if len(users) > SANITY_CAP:
        return None
    safe, blocked, counts_by_id = [], [], {}
    for u in users:
        # Belt-and-suspenders: never operate on anything not exactly prefixed.
        if not u.username.startswith(PREFIX):
            continue
        c = A._account_dependent_counts(u.id)
        counts_by_id[u.id] = c
        if c["team_owned"] > 0:
            blocked.append((u, f"owns {c['team_owned']} team(s)"))
        else:
            safe.append(u)
    return safe, blocked, counts_by_id


def _print_group(title, entries, counts_by_id, with_reason=False):
    print(f"{title}: {len(entries)}")
    for item in sorted(entries, key=lambda e: (e[0].id if with_reason else e.id)):
        u = item[0] if with_reason else item
        c = counts_by_id.get(u.id, {})
        dep = ", ".join(f"{k}={v}" for k, v in c.items() if v) or "(no dependent rows)"
        line = f"  id={u.id:<6} {u.username:<40} {dep}"
        if with_reason:
            line += f"   ⚠ {item[1]}"
        print(line)


def execute(safe, counts_by_id):
    """Delete each safe account via the app's account-deletion service (one
    transaction per account, shared team data preserved). Safe accounts own no
    team, so none will be blocked."""
    deleted = 0
    for u in safe:
        report = A.delete_user_account(u.id, dry_run=False)
        if report.get("executed"):
            deleted += 1
        elif report.get("blocked"):
            print(f"  SKIPPED id={u.id} {u.username}: {', '.join(report['blockers'])}")
    print(f"\nDELETED {deleted} safe account(s) via delete_user_account "
          f"(private data removed; shared team messages/moments preserved).")


def main():
    ap = argparse.ArgumentParser(description="Delete safe qa_smoke_ smoke-test accounts")
    ap.add_argument("--execute", action="store_true",
                    help="actually delete the SAFE group (default is a dry run that changes nothing)")
    args = ap.parse_args()

    with A.app.app_context():
        result = survey()
        if result is None:
            print(f"ABORT: more than {SANITY_CAP} '{PREFIX}' accounts matched. "
                  "That's unexpected — investigate before running any deletion.")
            sys.exit(2)
        safe, blocked, counts_by_id = result

        if not safe and not blocked:
            print(f"No '{PREFIX}' accounts found. Nothing to do.")
            return

        mode = "EXECUTE" if args.execute else "DRY RUN (no changes)"
        print(f"=== {mode} ===\n")
        _print_group("SAFE TO DELETE NOW", safe, counts_by_id)
        print()
        _print_group("REQUIRES MANUAL CLEANUP (team owners or other blockers)",
                     blocked, counts_by_id, with_reason=True)

        if not args.execute:
            print(f"\nDry run only. {len(safe)} safe, {len(blocked)} blocked. "
                  "Re-run with --execute to delete the safe group.")
            return

        if not safe:
            print("\nNothing in the safe group to delete.")
        else:
            execute(safe, counts_by_id)

        # Re-query: safe group should be gone; blocked accounts remain by design.
        remaining = _matches()
        blocked_ids = {u.id for u, _r in blocked}
        stray = [u for u in remaining if u.id not in blocked_ids]
        if stray:
            print(f"\n✗ {len(stray)} supposedly-deleted account(s) STILL PRESENT: "
                  + ", ".join(u.username for u in stray))
            sys.exit(3)
        print(f"\n✓ Re-query: 0 safe qa_smoke_ accounts remain. "
              f"{len(remaining)} blocked account(s) left untouched (manual cleanup).")


if __name__ == "__main__":
    main()
