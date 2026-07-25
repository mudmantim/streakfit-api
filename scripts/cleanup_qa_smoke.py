#!/usr/bin/env python3
"""Delete StreakFit smoke-test accounts (username prefix 'qa_smoke_') and their
dependent rows. DRY RUN by default — pass --execute to actually delete.

Safety guarantees:
  * EXACT Python prefix match on 'qa_smoke_' — never a SQL LIKE (SQL '_' is a
    single-char wildcard, which would be a substring match; explicitly avoided).
  * Each matched username must parse as qa_smoke_<epoch>_<tag>; anything that does
    not is treated as AMBIGUOUS and aborts the whole run (no partial deletes).
  * Aborts if any matched account CREATED a Team — tearing a team down would touch
    other users' data and is out of scope here.
  * Aborts if the match count exceeds a sanity cap.
  * Deletion runs in a single transaction (all-or-nothing), then re-queries to
    confirm zero remain. Touches no migrations and no application behavior.

Run where the target DATABASE_URL is set (e.g. production):
  DATABASE_URL=... SECRET_KEY=... JWT_SECRET_KEY=... python scripts/cleanup_qa_smoke.py            # dry run
  DATABASE_URL=... SECRET_KEY=... JWT_SECRET_KEY=... python scripts/cleanup_qa_smoke.py --execute  # delete
"""
import argparse
import datetime
import sys

import app as A

PREFIX = "qa_smoke_"
SANITY_CAP = 200   # refuse to run if more than this many match — a bug would be scary at scale

# Every table that references user.id, as (label, model, fk-attribute).
_DEPENDENTS = [
    ("challenge",          A.Challenge,       "user_id"),
    ("daily_completion",   A.DailyCompletion, "user_id"),
    ("brain_boost_answer", A.BrainBoostAnswer, "user_id"),
    ("progress_event",     A.ProgressEvent,   "user_id"),
    ("team_membership",    A.TeamMembership,  "user_id"),
    ("team_message",       A.TeamMessage,     "sender_user_id"),
    ("team_moment",        A.TeamMoment,      "subject_user_id"),
    ("coach_turn",         A.CoachTurn,       "user_id"),
    ("coach_note",         A.CoachNote,       "user_id"),
]
# team.created_by_user_id is handled separately as a HARD STOP (see below).


def _created_from_username(username):
    """qa_smoke_<epoch>_<tag> -> UTC datetime, or None if it doesn't parse (which
    means the account is NOT a well-formed smoke account and we must not touch it)."""
    rest = username[len(PREFIX):]
    epoch = rest.split("_", 1)[0]
    if not epoch.isdigit():
        return None
    try:
        return datetime.datetime.utcfromtimestamp(int(epoch))
    except (ValueError, OverflowError, OSError):
        return None


def _matches():
    """Fetch ALL users and filter in Python with an exact prefix — deliberately no
    SQL LIKE, so there is zero chance of a wildcard/substring match."""
    return [u for u in A.User.query.all() if u.username.startswith(PREFIX)]


def _counts(user):
    counts = {}
    for label, model, attr in _DEPENDENTS:
        counts[label] = model.query.filter(getattr(model, attr) == user.id).count()
    counts["team_created"] = A.Team.query.filter(A.Team.created_by_user_id == user.id).count()
    return counts


def survey():
    """Return (users, counts_by_id, ok, reasons). ok=False means DO NOT DELETE."""
    users = _matches()
    reasons = []
    if not users:
        return users, {}, True, reasons
    if len(users) > SANITY_CAP:
        reasons.append(f"{len(users)} matches exceeds sanity cap {SANITY_CAP}")
        return users, {}, False, reasons

    counts_by_id = {}
    ok = True
    for u in users:
        # Belt-and-suspenders: never operate on anything not exactly prefixed.
        if not u.username.startswith(PREFIX):
            reasons.append(f"id={u.id} '{u.username}' does not match exact prefix")
            ok = False
            continue
        counts_by_id[u.id] = _counts(u)
        if _created_from_username(u.username) is None:
            reasons.append(f"id={u.id} '{u.username}' has no parseable epoch (ambiguous)")
            ok = False
        if counts_by_id[u.id]["team_created"] > 0:
            reasons.append(f"id={u.id} '{u.username}' created {counts_by_id[u.id]['team_created']} team(s)")
            ok = False
    return users, counts_by_id, ok, reasons


def print_survey(users, counts_by_id):
    print(f"Matched {len(users)} account(s) with exact prefix '{PREFIX}':\n")
    for u in sorted(users, key=lambda x: x.id):
        created = _created_from_username(u.username)
        created_s = (created.isoformat() + "Z") if created else "UNPARSEABLE"
        c = counts_by_id.get(u.id, {})
        dep = ", ".join(f"{k}={v}" for k, v in c.items() if v) or "(no dependent rows)"
        print(f"  id={u.id:<6} {u.username:<36} created={created_s}")
        print(f"           dependents: {dep}")


def execute(users, counts_by_id):
    total_dep = 0
    per_table = {label: 0 for label, _m, _a in _DEPENDENTS}
    try:
        for u in users:
            for label, model, attr in _DEPENDENTS:
                n = model.query.filter(getattr(model, attr) == u.id).delete(synchronize_session=False)
                per_table[label] += n
                total_dep += n
            A.db.session.delete(u)
        A.db.session.commit()
    except Exception:
        A.db.session.rollback()
        print("ERROR during deletion — rolled back, nothing deleted.")
        raise

    print(f"\nDELETED {len(users)} account(s) and {total_dep} dependent row(s).")
    print("  by table: " + ", ".join(f"{k}={v}" for k, v in per_table.items() if v) or "  (none)")
    remaining = _matches()
    if remaining:
        print(f"\n✗ {len(remaining)} qa_smoke_ account(s) STILL PRESENT: "
              + ", ".join(x.username for x in remaining))
        sys.exit(3)
    print("\n✓ Re-query confirms zero qa_smoke_ accounts remain.")


def main():
    ap = argparse.ArgumentParser(description="Delete qa_smoke_ smoke-test accounts")
    ap.add_argument("--execute", action="store_true",
                    help="actually delete (default is a dry run that changes nothing)")
    args = ap.parse_args()

    with A.app.app_context():
        users, counts_by_id, ok, reasons = survey()
        if not users:
            print("No qa_smoke_ accounts found. Nothing to do.")
            return
        mode = "EXECUTE" if args.execute else "DRY RUN (no changes)"
        print(f"=== {mode} ===\n")
        print_survey(users, counts_by_id)
        if not ok:
            print("\nABORT — not safe to delete:")
            for r in reasons:
                print(f"  - {r}")
            print("Resolve the flagged accounts manually; no deletion performed.")
            sys.exit(2)
        print(f"\nAll {len(users)} match the exact prefix, parse as synthetic "
              f"qa_smoke_<epoch>_<tag>, and created no teams — clearly smoke accounts.")
        if args.execute:
            execute(users, counts_by_id)
        else:
            print("\nDry run only. Re-run with --execute to delete.")


if __name__ == "__main__":
    main()
