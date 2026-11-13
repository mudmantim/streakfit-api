#!/usr/bin/env python3
"""Delete StreakFit smoke-test accounts (username prefix 'qa_smoke_') and their
dependent rows. DRY RUN by default — pass --execute to actually delete.

Classification:
  * Any username beginning with the EXACT prefix 'qa_smoke_' is a QA account.
    (Exact Python prefix match — never a SQL LIKE, whose '_' is a wildcard.)
  * SAFE to delete: a QA account the app's deletion service would delete.
  * BLOCKED (requires manual cleanup): whatever that service refuses — a QA account
    still in a team it created WITH other people (tearing that team down would
    touch other users' data), or one with guardian/consent/permission-audit rows.
    Left untouched here. Since the owner decision of 2026-11-13 the service itself
    deletes a team its owner is the only member of (or that is empty) together
    with the owner, and hands a team the owner already left to another member --
    so such owners are SAFE, and --execute removes those teams with them.
  * NOTE: deleting an account that FILED a report or an appeal cuts the link to it
    (the report stays). Once any such link is cut, migration 08920334bccd can no
    longer be downgraded -- see docs/operations/deploy-account-deletion-2026-09.md.

Behavior:
  * Dry run lists both groups and changes nothing.
  * --execute deletes ONLY the safe group, ONE TRANSACTION PER ACCOUNT (each
    account is all-or-nothing; the run as a whole is not), then re-queries and
    reports both groups. Blocked accounts are left in place, with the reason.
  * Aborts entirely only if the match count exceeds a sanity cap (a matching bug
    at scale would be dangerous). Touches no migrations and no app behavior.

Team-owner policy (--team-owner-policy=delete-qa-team), opt-in:
  * For each BLOCKED account whose ONLY blocker is team ownership, every team it
    owns is checked by the app's delete_qa_smoke_team service: the team name
    starts with "Smoke Test ", the owner and every person linked to the team is
    a qa_smoke_ account, and no report about the team is pending or held.
  * Dry run (no --execute) lists what would happen and changes nothing.
  * With --execute: each team is deleted in its own transaction, then the owner
    in its own transaction through delete_user_account (the team is gone, so
    team ownership no longer blocks). Reports and moderation actions about the
    team are kept, with their team link cut. Prints ids and counts only.
  * A second sanity cap (TEAM_SANITY_CAP) aborts before anything if more teams
    than that would be deleted.

Run where the target DATABASE_URL is set (e.g. production):
  DATABASE_URL=... SECRET_KEY=... JWT_SECRET_KEY=... python scripts/cleanup_qa_smoke.py            # dry run
  DATABASE_URL=... SECRET_KEY=... JWT_SECRET_KEY=... python scripts/cleanup_qa_smoke.py --execute  # delete safe
  ... python scripts/cleanup_qa_smoke.py --team-owner-policy=delete-qa-team            # dry run, incl. QA teams
  ... python scripts/cleanup_qa_smoke.py --team-owner-policy=delete-qa-team --execute  # also QA teams + owners
Against PRODUCTION from a laptop, also set STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE=1 for that one command
(db_guard R5); without it the app refuses to connect.
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
TEAM_SANITY_CAP = 100   # --team-owner-policy: refuse if more teams than this would be deleted
TEAM_OWNER_POLICY = "delete-qa-team"
DOWNGRADE_CAVEAT = (
    "NOTE: deleting an account that FILED a report or an appeal cuts its link to it "
    "(the report stays). Once any such link is cut, migration 08920334bccd can no "
    "longer be downgraded -- roll forward instead; see "
    "docs/operations/deploy-account-deletion-2026-09.md.")

def _matches():
    """Fetch ALL users and filter in Python with an exact prefix — deliberately no
    SQL LIKE, so there is zero chance of a wildcard/substring match."""
    return [u for u in A.User.query.all() if u.username.startswith(PREFIX)]


def survey():
    """Return (safe, blocked, counts_by_id). `blocked` is a list of (user, reason).
    Returns None if the sanity cap is exceeded (hard abort). Dependency counts and
    EVERY blocker come from the app's account-deletion service, so the dry run
    classifies exactly as --execute will -- this script keeps no rules of its own."""
    users = _matches()
    if len(users) > SANITY_CAP:
        return None
    safe, blocked, counts_by_id = [], [], {}
    for u in users:
        # Belt-and-suspenders: never operate on anything not exactly prefixed.
        if not u.username.startswith(PREFIX):
            continue
        plan = A.delete_user_account(u.id, dry_run=True)
        counts_by_id[u.id] = plan["counts"]
        if plan["blocked"]:
            blocked.append((u, "; ".join(plan["blockers"])))
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
    transaction per account, shared team data preserved). A safe account owns
    no team that anybody else is still in; a team only it is in goes with it."""
    deleted = 0
    for u in safe:
        report = A.delete_user_account(u.id, dry_run=False)
        if report.get("executed"):
            deleted += 1
        elif report.get("blocked"):
            print(f"  SKIPPED id={u.id} {u.username}: {', '.join(report['blockers'])}")
    print(f"\nDELETED {deleted} safe account(s) via delete_user_account "
          f"(private data removed; shared team messages/moments preserved).")


def plan_team_owners(blocked):
    """For --team-owner-policy=delete-qa-team. Returns (candidates, refused):
    candidates = [(user_id, [team_id, ...], [team dry-run report, ...])] -- every
    team clears delete_qa_smoke_team and team ownership is the account's ONLY
    blocker; refused = [(user_id, [reason codes])]. Read-only."""
    candidates, refused = [], []
    for u, _reason in sorted(blocked, key=lambda e: e[0].id):
        if not u.username.startswith(PREFIX):
            continue
        plan = A.delete_user_account(u.id, dry_run=True)
        codes = plan.get("blocker_codes", [])
        if codes != ["team_owned"]:
            refused.append((u.id, ["account:" + c for c in codes]))
            continue
        team_ids = [t.id for t in A.Team.query.filter(
            A.Team.created_by_user_id == u.id).order_by(A.Team.id)]
        reports = [A.delete_qa_smoke_team(tid, dry_run=True) for tid in team_ids]
        reasons = [f"team {r['team_id']}:{code}" for r in reports for code in r["refusals"]]
        if reasons or not team_ids:
            refused.append((u.id, reasons or ["no_team_found"]))
        else:
            candidates.append((u.id, team_ids, reports))
    return candidates, refused


def _print_team_plan(candidates, refused):
    n_teams = sum(len(t) for _u, t, _r in candidates)
    print(f"TEAM-OWNER POLICY '{TEAM_OWNER_POLICY}': {len(candidates)} owner(s), "
          f"{n_teams} QA-only team(s) eligible; {len(refused)} account(s) refused")
    for uid, _team_ids, reports in candidates:
        for r in reports:
            rows = ", ".join(f"{k}={v}" for k, v in r["counts"].items() if v) or "(no rows)"
            print(f"  owner id={uid:<6} team={r['team_id']:<6} linked_people="
                  f"{r['linked_people']} {rows}")
    for uid, reasons in refused:
        print(f"  REFUSED id={uid:<6} {'; '.join(reasons)}")
    filed = A.Report.query.filter(A.Report.reporter_user_id.in_(
        [uid for uid, _t, _r in candidates])).count() if candidates else 0
    print(f"  reports filed by these owners: {filed} (links cut on deletion)")
    print(f"  {DOWNGRADE_CAVEAT}")


def execute_team_policy(candidates):
    """One transaction per team, then one per owner. Returns the owner ids deleted."""
    deleted_owners, deleted_teams = [], 0
    for uid, team_ids, _reports in candidates:
        ok = True
        for tid in team_ids:
            r = A.delete_qa_smoke_team(tid, dry_run=False)
            if r["executed"]:
                deleted_teams += 1
            else:
                print(f"  SKIPPED team={tid} owner id={uid}: {', '.join(r['refusals'])}")
                ok = False
                break
        if not ok:
            continue
        rep = A.delete_user_account(uid, dry_run=False)
        if rep.get("executed"):
            deleted_owners.append(uid)
        else:
            print(f"  SKIPPED owner id={uid}: {', '.join(rep.get('blocker_codes', []))}")
    print(f"\nDELETED {deleted_teams} QA-only team(s) via delete_qa_smoke_team and "
          f"{len(deleted_owners)} owner account(s) via delete_user_account.")
    return deleted_owners


def main():
    ap = argparse.ArgumentParser(description="Delete safe qa_smoke_ smoke-test accounts")
    ap.add_argument("--execute", action="store_true",
                    help="actually delete the SAFE group (default is a dry run that changes nothing)")
    ap.add_argument("--team-owner-policy", choices=[TEAM_OWNER_POLICY], default=None,
                    help="also delete QA-only 'Smoke Test ' teams and then their qa_smoke_ owners")
    args = ap.parse_args()

    with A.app.app_context():
        if args.team_owner_policy and not args.execute \
                and A.db.session.get_bind().dialect.name == "postgresql":
            # Belt-and-braces for the new policy's dry run: the database itself
            # refuses any write in this transaction.
            A.db.session.execute(A.db.text("SET TRANSACTION READ ONLY"))
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

        candidates = []
        if args.team_owner_policy:
            candidates, refused = plan_team_owners(blocked)
            n_teams = sum(len(t) for _u, t, _r in candidates)
            if n_teams > TEAM_SANITY_CAP:
                print(f"ABORT: {n_teams} teams would be deleted, more than {TEAM_SANITY_CAP}. "
                      "That's unexpected — investigate before running any deletion.")
                sys.exit(2)
            print()
            _print_team_plan(candidates, refused)

        if not args.execute:
            if args.team_owner_policy:
                A.db.session.rollback()
                print(f"\nDry run only. {len(safe)} safe, {len(blocked)} blocked, "
                      f"{len(candidates)} blocked owner(s) eligible under '{TEAM_OWNER_POLICY}'. "
                      "Re-run with --execute to delete.")
                return
            print(f"\nDry run only. {len(safe)} safe, {len(blocked)} blocked. "
                  "Re-run with --execute to delete the safe group.")
            return

        if not safe:
            print("\nNothing in the safe group to delete.")
        else:
            execute(safe, counts_by_id)

        policy_deleted = execute_team_policy(candidates) if candidates else []

        # Re-query: safe group should be gone; blocked accounts remain by design.
        remaining = _matches()
        blocked_ids = {u.id for u, _r in blocked} - set(policy_deleted)
        stray = [u for u in remaining if u.id not in blocked_ids]
        if stray:
            print(f"\n✗ {len(stray)} supposedly-deleted account(s) STILL PRESENT: "
                  + ", ".join(u.username for u in stray))
            sys.exit(3)
        print(f"\n✓ Re-query: 0 safe qa_smoke_ accounts remain. "
              f"{len(remaining)} blocked account(s) left untouched (manual cleanup).")
        if args.team_owner_policy:
            left = A.Team.query.filter(
                A.Team.created_by_user_id.in_([u.id for u in remaining])).count() if remaining else 0
            print(f"✓ Re-query: {len(policy_deleted)} owner(s) deleted under "
                  f"'{TEAM_OWNER_POLICY}'; {left} team(s) still owned by qa_smoke_ accounts. "
                  f"Remaining blocked ids: {sorted(u.id for u in remaining)}")
            print(DOWNGRADE_CAVEAT)


if __name__ == "__main__":
    main()
