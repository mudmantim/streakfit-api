# ADR-0007: QA cleanup safe-vs-blocked philosophy
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
The verification suite creates throwaway accounts with the username prefix
`qa_smoke_` (and disposable `Smoke Test <tag>` teams). These accumulate and need
periodic cleanup against real databases, including production. A cleanup script
that deletes accounts is inherently dangerous: a matching bug or a careless
delete could remove a *real* user, and some smoke accounts own teams whose
teardown would touch other users' shared data. The script has to be built so that
the worst plausible mistake is "did nothing," never "deleted a real account."

## Decision
`scripts/cleanup_qa_smoke.py` is built around fail-safe classification:

- **Exact Python prefix match, never SQL `LIKE`.** It fetches all users and
  filters in Python with `username.startswith("qa_smoke_")`. A SQL `LIKE
  'qa_smoke_%'` would treat `_` as a single-character wildcard and could match
  unintended usernames; the Python prefix has zero wildcard semantics. The match
  is even re-checked per user before any action (belt-and-suspenders).
- **Two groups: SAFE vs BLOCKED.** SAFE = a `qa_smoke_` account owning no team
  (deletable). BLOCKED = a `qa_smoke_` account that owns a team, requiring manual
  cleanup because tearing the team down would touch other users' data. The
  ownership check reuses the app's `_account_dependent_counts`.
- **Dry-run by default; `--execute` deletes only SAFE.** With no flag it lists
  both groups and changes nothing. `--execute` deletes only the SAFE group, each
  via `delete_user_account(..., dry_run=False)` (one transaction per account,
  shared team data preserved — see [ADR-0006](0006-application-level-account-deletion.md)),
  then re-queries and reports. BLOCKED accounts are always left in place.
- **`SANITY_CAP = 1000` hard abort.** If more than 1000 accounts match the
  prefix, the script refuses to run at all and exits non-zero — an unexpectedly
  large match is treated as a possible bug, not a big cleanup.

## Alternatives considered
- **SQL `LIKE 'qa_smoke_%'` matching.** Why it lost: `_` is a wildcard in `LIKE`,
  so the pattern can match usernames the author didn't intend; the whole point is
  a match with no wildcard semantics. Python `startswith` is exact.
- **Delete-by-default (no dry-run).** Why it lost: a destructive script against
  production must show its plan first; dry-run-by-default means the harmless
  outcome is the default outcome.
- **Delete team owners too** (cascade their teams). Why it lost: a smoke account
  that owns a team may share it with real or other test data; tearing it down is
  exactly the shared-data risk [ADR-0006](0006-application-level-account-deletion.md)
  refuses. Blocking and reporting is the safe treatment.
- **No cap.** Why it lost: without `SANITY_CAP`, a classification bug that
  suddenly matches thousands of rows would execute at full scale. The cap turns
  "surprisingly many matches" into a hard stop.

## Why the current solution won
Every layer is chosen so that the failure mode is inaction, not destruction:
exact matching removes wildcard surprises, dry-run-by-default makes you opt in to
deletion, the SAFE/BLOCKED split keeps shared-data teardown off the table, the
cap stops runaway matches, and delegating the actual delete to
`delete_user_account` means the script inherits that service's transactional,
shared-data-preserving guarantees instead of reinventing them.

## Consequences & future tradeoffs
- **Makes easy:** safe, repeatable smoke-account cleanup against real/production
  databases; a clear report of what will and won't be touched before anything is.
- **Makes hard:** BLOCKED (team-owning) smoke accounts are never auto-cleaned —
  they need a manual step, so they can accumulate. Fetching all users to filter
  in Python is O(users) rather than an indexed query; fine at current scale,
  bounded by the sanity cap, but not free at very large user counts.
- **When we'd revisit:** if team-owning smoke accounts become common, add an
  explicit `allow_team_owner` cleanup path that first reassigns/deletes the QA
  team. If the user table grows huge, the all-users fetch would need narrowing —
  but not by switching to a `LIKE` that reintroduces wildcard risk.

## Code references
- `scripts/cleanup_qa_smoke.py:34-35` — `PREFIX = "qa_smoke_"`, `SANITY_CAP = 1000`.
- `scripts/cleanup_qa_smoke.py:37-40` — `_matches`: fetch all, filter with exact
  Python `startswith`, comment explaining why not SQL `LIKE`.
- `scripts/cleanup_qa_smoke.py:43-62` — `survey`: sanity-cap hard abort, per-user
  re-check, SAFE vs BLOCKED split via `_account_dependent_counts`.
- `scripts/cleanup_qa_smoke.py:77-89` — `execute`: SAFE-only deletion via
  `delete_user_account(dry_run=False)`.
- `scripts/cleanup_qa_smoke.py:92-136` — `main`: dry-run default, `--execute`
  gate, re-query verification that strays fail loudly.
- Tests: `tests/test_cleanup_qa_smoke.py`. See
  [`../architecture/verification-suite.md`](../architecture/verification-suite.md).
