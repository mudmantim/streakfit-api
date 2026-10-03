# RC-B2a — concurrency and data integrity: what ships, how to deploy, how to roll back

Repair tranche B2a, on top of production `428a91e` (RC-B1, Alembic head
`08920334bccd`). Candidate: branch `rc-b2a` @ `e9efb12`. Emergency rollback
build: branch `rollback/rc-b2a-compat` @ `174cfe1`.
**Not deployed.** Running the precondition query, deploying and any rollback
each need owner approval. B2b (D6, the day boundary) is separate and not here.

## The operational invariant stays

The owner decision stands: production remains **one** instance, `WEB_CONCURRENCY`
= 1, **one** synchronous Gunicorn worker, no `--threads`. B2a removes the
reasons that invariant was *required* (D26–D36, D37), and the lab now proves
the app on three workers and on two app processes sharing one database — but
lifting the invariant is a separate decision, not part of this release.

## What ships

| item | before (428a91e, reproduced) | after |
|---|---|---|
| D26 | two filter purchases at once overspend acorns | user lock; `0 <= acorns_spent <= acorns_total` enforced by the database |
| D27a/c | parallel awards (same or different routes) lose XP/acorn updates | every award runs under the person's row lock, one commit per request |
| D27b | mission + perfect bonus paid twice | paid by inserting a `daily_mission_award (user_id, mission_date)` row (primary key) |
| D28 | two joiners take the last seat: 9 on an 8-seat team | person lock, then team lock; cap counted under the lock |
| D29 | same exercise twice at once: one 500 | idempotent 200, awards nothing the second time (owner decision) |
| D30 | same person joins twice at once: one 500 | 400 "Already a member" |
| D31 | same account deleted twice at once: one 500 | 404 |
| D32 | campfire crossings at once: lost log, duplicated/missing stage moment and Rickie message | atomic `UPDATE … RETURNING`; every side effect keyed off the returned total |
| D35 | same team challenge completed twice: one 500 | 200 `already_completed` |
| D36 | challenge daily reward cap exceeded | counted under the user lock |
| S4 (lock-order review) | two teammates in the same teams finishing together **deadlock** (40P01, a 500, campfire contribution lost) | memberships taken in ascending team id; clean wait |
| D37 | `gunicorn --preload`: the head check's pooled Postgres connection inherited by workers — **one socket in two processes** (seen on the 1-worker production mirror: master, which runs the retention sweeper, and the worker). With 3 workers: 13 unhandled `PGRES_TUPLES_OK` 500s + 1 worker timeout in 60 parallel reads | engine disposed after the head check; every forked child drops inherited connections (`dispose(close=False)`). Separate commit `2dc41f4`, can ship alone |
| review follow-ups | leave / remove / rotate-invite took no lock: a join in flight could land with a code the creator was just told is dead; history could show `member_left` before the member's own campfire log | person lock(s) then team lock; deadlock (40P01) answers the same retryable JSON 503 as a lock timeout |

Also: `SET LOCAL lock_timeout = '5s'` on every request path that takes the user
lock (a stuck holder → JSON 503 `busy`, never a hung worker), and on the
migration itself.

Not defects / not fixed (recorded, owner's call):

- **D33** (per-user team-count cap race) — NOT REPRODUCED: `TEAM_FREE_TEAM_COUNT_CAP = None` on every plan, so there is nothing to exceed. Latent: if a cap returns, `create_team` must take `_lock_user` first.
- **D34** (Brain Boost answered twice) — never a data defect: the unique constraint already held; it only lacked the ordering. Now ordered under the user lock. (Commit `3689b9f` says "Repairs … D34"; that overstates it.)
- **D38** — `_lock_moderation_subject` always emitted `FOR NO KEY UPDATE`, not the `FOR KEY SHARE` its docstring claimed. Stronger, never weaker; docstring corrected, behaviour unchanged.
- **LC-1** (pre-existing, Low–Medium) — five routes that write rows referencing the person without taking the user lock (team message, team photo, create team, personal challenge, daily effort) answer a generic **500** if they race *that person's own* account deletion; appeals answer a wrong 409. No orphans, no data loss. Proposed for B2b or later.
- **S1, S2** (pre-existing, Low) — two deadlocks reproduced on both builds: account deletion vs the hourly evidence sweep; Ask Rickie's expired-turn delete vs the retention thread's. The victim is the background pass (it retries next hour) or a retried deletion. Proposed for later.
- **lock_timeout coverage** — only the `_lock_user` paths are bounded. Deletion, moderation-subject locks, admin report/appeal locks and the sweeper wait without limit (bounded in practice by short transactions and Gunicorn's 30 s timeout). No `statement_timeout` / `idle_in_transaction_session_timeout` is set. Proposed for later.
- **CHECK-violation answers** (latent, needs a future path that skips the lock) — a purchase that trips the acorns CHECK answers 409 `already_unlocked`; an award that trips one answers a generic 500. Clean rollback in both.

## Evidence (all under `e2e-campaign/evidence/rcb2a/`)

- **Red, production code:** `red_428a91e/run1-6.json` (original), and `harness_e9efb12_on_428a91e.json` — the final harness on 428a91e source: 23/23 red.
- **Green, deterministic PostgreSQL harness** (`tests/concurrency_race.py`, 23 scenarios): `green/e9efb12_run1-3.json`, three fresh databases, 23/23 each. Green = invariants AND the second request observed in `pg_stat_activity` / `pg_blocking_pids` waiting on the intended lock (exact strength and table) or, where the claim is the absence of a wait, completing while the first is held. A hold released by its safety timeout always fails.
- **Harness power** (`mutations/mut_A,B,C,R.json`): removing the user lock → 15 red; plain `FOR UPDATE` → 19; a commit inside `award_progress` → 3; unlocked leave/rotate → 2.
- **Unit:** `tests/test_lock_strength_cache.py` (SQLAlchemy 2.0's cache key ignores `key_share`, which silently turned deletion's `FOR UPDATE` into `FOR NO KEY UPDATE` — found by the harness, fixed with `of=`); `tests/test_fork_safe_db_pool.py` (D37, red on 3689b9f).
- **Suites at e9efb12:** pytest 1208 passed / 28 skipped (skips = PostgreSQL-only); PostgreSQL deletion suite 16/16; ruff, mypy, build check clean; uicheck 287/0.
- **Lanes at e9efb12** (`lanes_e9efb12/`): L = 3 workers, L2 = 1 worker (production mirror), L3 = two app processes on one database. `verify_all` 188/188 on each; HTTP burn-in 17/17 on each, zero tracebacks; no Postgres socket shared between processes on any lane. (W10: the burn on L2 cannot detect races — it is a regression check; the deterministic harness is the concurrency evidence.)
- **Migration:** `migration_rehearsal_pg17.json` + `.py` (empty→head parity incl. CHECK definitions, upgrade with data, all four constraints enforce, downgrade, re-upgrade, an impossible balance fails the upgrade atomically); `migration_lock_timeout_rehearsal.json` + `.py` (behind a held row lock the upgrade fails in 6.4 s, stays at 08920334bccd, retry succeeds).
- **Rollback:** `rollback/` — compat booted on a database populated by the candidate, at head; deleted a user the candidate had paid a mission bonus (200, gone); `verify_all` 188/188 (71ce3eb and 174cfe1); roll-forward booted at head.
- **Independent reviews:** `reviews/{economy,rewards_campfire,teams,lifecycle,lock_order,migration_rollback,w10}/REPORT.md` and their scripts. Every D-item confirmed fixed by at least one reviewer who re-derived it; W10 re-ran red and green itself and agreed.

Correction: commit `2dc41f4`'s message says "15 of 60 parallel GET /api/me answered 500"; that figure was the client tally, whose raw output was not kept. The saved server log shows 13 unhandled `PGRES_TUPLES_OK` errors plus 1 worker timeout. The defect is the same either way.

Verify-suite note: two `verify_all` runs on the same lane less than a minute apart fail `auth.*` with 429 (5 registrations/minute per IP). A suite pacing issue, not a product one; production runs are one-off.

## Before deploy — precondition (read-only, owner runs it)

Immediately before deploying, against production:

```sql
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '15s';
SELECT (SELECT version_num FROM alembic_version) AS alembic_version,
       to_regclass('public.daily_mission_award') IS NULL AS award_table_absent,
       (SELECT count(*) FROM pg_constraint WHERE conrelid='"user"'::regclass
          AND conname IN ('ck_user_xp_nonnegative','ck_user_acorns_nonnegative',
                          'ck_user_acorns_spent_within_earned')) AS name_collisions;
SELECT count(*) AS users,
       count(*) FILTER (WHERE xp_total < 0)                AS v_xp_negative,
       count(*) FILTER (WHERE acorns_total < 0)            AS v_acorns_negative,
       count(*) FILTER (WHERE acorns_spent < 0)            AS v_spent_negative,
       count(*) FILTER (WHERE acorns_spent > acorns_total) AS v_spent_over_earned
FROM "user";
SELECT pid, state, now()-xact_start AS xact_age, left(query,60) FROM pg_stat_activity
WHERE datname=current_database() AND pid<>pg_backend_pid()
  AND xact_start IS NOT NULL AND now()-xact_start > interval '5 seconds';
ROLLBACK;
```

**GO** only if: `alembic_version = 08920334bccd`, `award_table_absent` true,
`name_collisions` 0, all four `v_*` 0, and the last query returns no rows.
**NO-GO:** any `v_*` > 0 → do not deploy and do not edit balances to pass —
repairing a balance is an owner data decision against the `progress_event`
ledger (ids-only query in `reviews/migration_rollback/REPORT.md`). Wrong version
or table present → stop. Long transactions → wait and re-run.
Safety net if a violation appears after the check: the upgrade fails atomically,
Gunicorn never starts, 428a91e keeps serving.

## Deploy sequence

1. Fresh encrypted backup (custody tooling); confirm production is `428a91e` at `08920334bccd` on live `/admin`.
2. Precondition query → GO.
3. Deploy `e9efb12` with the **unchanged** Start Command (`flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app`); instance count, plan and worker settings untouched.
4. Deploy log: `Running upgrade 08920334bccd -> c7d2a9e41b30`, then `Database migration check passed (at head c7d2a9e41b30)`.
5. After: `/admin` shows `e9efb12` at head; the three CHECKs `convalidated`; `daily_mission_award` exists; one `verify_all` run; dismiss its synthetic smoke reports as before; phone check.
6. Keep `174cfe1` ready.

## Rollback runbook

- **A (default): deploy compat `174cfe1`, leave the database at head.** Its upgrade is a no-op and the head check passes; it never writes `daily_mission_award` but deletes those rows with an account. A mission bonus cannot be paid twice across rollback/roll-forward (today's completion count reaches five at most once; reviewed and tested across five build orders).
- **B: never deploy plain `428a91e` while the database is at head** — `flask db upgrade` fails ("Can't locate revision") and, if bypassed, account deletion 500s on the new foreign key.
- **C (only if compat itself is broken):** get compat live, then `flask db downgrade 08920334bccd` run from compat or candidate code (one transaction; loses only `daily_mission_award` rows), then deploy `428a91e` immediately.
- **Roll forward:** after A, redeploy `e9efb12`. After C, re-run the precondition query first.

## Decisions for the owner

1. Approve deploying RC-B2a `e9efb12` (with D37), subject to the precondition GO.
2. Or: ship D37 alone first — it is the only fix that changes today's one-worker production behaviour (the shared master/worker socket); D26–D36 protect a future scale-up. That build (`428a91e` + a cherry-pick of `2dc41f4`, no migration) is not prepared yet; it is a one-commit cherry-pick that applies cleanly (it already did onto the compat branch).
3. Whether LC-1, S1/S2 and the wider lock_timeout coverage go into B2b or a later tranche.
