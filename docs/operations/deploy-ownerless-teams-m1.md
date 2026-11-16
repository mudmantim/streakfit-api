# Deploy: ownerless teams (migration M1, `a1c4e0f2b7d9`)

**Status: NOT APPLIED to production.** Running this migration on production
is a separate owner OK (decision packet, 2026-11-14/15). Tested only on local
SQLite and a disposable PostgreSQL 17.10 container.

**What it is.** Owner decision #1 as decided by Tim (P11 "M1 only", P1, P3,
P4, P4-safety (b), P7, P9). One schema change:

```
ALTER TABLE team ALTER COLUMN created_by_user_id DROP NOT NULL
```

`team.created_by_user_id IS NULL` means **the team has no owner**. Revision
`a1c4e0f2b7d9`, revises `d4f1b6e2a9c3` (the single head after it is
`a1c4e0f2b7d9`). **No backfill:** every existing team keeps its owner.
M2 (the ownership-claim table) is NOT part of this and was not written.

## Read this first: an ownerless team may stay read-only indefinitely

Say this plainly in the release notes and anywhere users are told about it:

- A team becomes **ownerless** when its owner leaves choosing "nobody", or
  when an owner deletes their account and no member is a verified adult, or
  when a sole owner leaves or deletes their account while a report or legal
  hold keeps the team.
- An ownerless team is **read-only**: no new chat messages, photos,
  challenges, challenge completions, campfire logs, team history or Rickie
  posts, and nobody can join it (its code answers like an unknown code).
  Members can still read it, report, leave, and delete their accounts.
- **There is no way back to having an owner yet.** Nothing records anyone's
  age (P2 deferred), and the claim process (M2, P5) is not built. **So an
  ownerless team may stay read-only with no end date.** The in-app notice
  says exactly that:

  > This team has no owner right now, so it's read-only: nobody can post,
  > share photos, start or complete challenges, or join, and missions no
  > longer add to its campfire. You can still read everything, report a
  > problem, leave the team or delete your account. StreakFit can't give a
  > team a new owner yet, so it may stay read-only with no end date.

- **Today every handover is refused** (P1: verified adults only, and no age is
  recorded). Every owner who leaves a team that other people are in makes it
  ownerless. "Hand over the team" stays in the app for when P2 ships.

An empty ownerless team is deleted by the retention sweeper (one team per
transaction, after the legal-hold fence and the team lock), once no report
about it is pending or under legal hold. Its passes are recorded as
`retention_run.kind = 'ownerless_reap'`, with detail
`deleted=N kept_for_review=N skipped=N failed=N`.

- The cleanup **selects only teams it may delete**: a team a pending report or
  a legal hold keeps is never selected (it is counted in `kept_for_review`),
  and each pass walks the candidates page by page, so kept teams cannot fill
  the batch and hide the deletable ones behind them (review R6-2(a)). Up to 50
  deletions per pass.
- **One failing team does not stop the pass** (review R6-2(b)): it is rolled
  back whole, logged as `event=ownerless_team_reap_failed team_id=<id>
  error=<type>`, counted in `failed=`, and the pass goes on. A pass stops after
  10 failures (a systemic fault).
- **A stuck team is visible:** the self-check `retention.ownerless_teams`
  (`/api/verification/self`) FAILs while the last unattended pass recorded
  `failed>0`, when a pass raised, or when no pass ran for 48 h; UNKNOWN before
  the first unattended pass.
- The sole-owner leave copy ("...kept only until that ends and then
  deleted") is true **only while this cleanup runs and keeps up**: it needs the
  retention sweeper running in production (C3/C4), and a team that keeps
  failing needs somebody to act on the FAIL above.

## Lock risk

- `DROP NOT NULL` is catalog-only (no table rewrite, no scan), but takes
  **ACCESS EXCLUSIVE on `team`**. Behind any held team row lock (account
  deletion's `FOR UPDATE`, a join's or leave's `FOR NO KEY UPDATE`, a post's
  `FOR SHARE`) it would queue, and every team query would queue behind it.
- The migration sets `SET LOCAL lock_timeout = '5s'`, so it **stops instead of
  stalling**. Measured on PostgreSQL 17.10 (2026-11-15), with one team row
  held `FOR UPDATE` by another session: the upgrade failed in **6.3 s** with
  `canceling statement due to lock timeout`, and the database stayed at
  `d4f1b6e2a9c3` with the column still NOT NULL. Unblocked, the upgrade took
  **1.3 s** wall-clock (5,000 teams; most of it is process start-up).
  Pinned by `tests/test_migrations_postgres.py::test_m1_fails_fast_behind_a_held_team_lock_and_changes_nothing`.
- If the Pre-Deploy `flask db upgrade` fails on the lock timeout, the deploy
  stops and the old build keeps serving on the old schema. **Simply deploy
  again**, ideally at a quiet hour.
- Migrate over the unpooled `MIGRATION_DATABASE_URL` that `db_guard` already
  enforces (`migrations/env.py`). **Take a Neon branch or restore point
  first** (an owner operation).

## Deploy order

1. (Owner) Neon branch / restore point.
2. (Owner) Set `STREAKFIT_QA_PROVISIONING_SECRET` on Render (and in the shell
   that runs `verify_all.py` / `post_deploy_check.py` over HTTP), or those
   tools can no longer register their `qa_smoke_` accounts. See
   `environment.md`.
3. Deploy the build. Its Pre-Deploy `flask db upgrade` applies `a1c4e0f2b7d9`
   before the new code takes traffic. During the window the **old build runs
   on the new schema: that is safe** -- the old code never writes NULL, and
   reads a NULL owner (there are none yet) as "not the owner".
4. Check: `/api/build-identity` shows `a1c4e0f2b7d9` and `atHead: true`;
   `verify_all` (**suite 19**, from this build) passes, including
   `security.handover_to_unconfirmed_age_refused`,
   `security.leave_handing_to_unconfirmed_age_refused` and
   `security.smoke_team_left_owned`; the sweeper records `ownerless_reap`
   runs and `retention.ownerless_teams` is PASS.
   **This step leaves no team ownerless, so it does not close the rollback**
   (review R6-1). Suite 19 never makes a team ownerless: it ends with its
   smoke team still owned by its own `qa_smoke_` account (kept, because its
   Moderation checks file pending reports about it). The ownerless path
   (`security.owner_leaves_with_no_owner`, `security.ownerless_team_*`,
   `security.last_member_leaves_and_deletes`) is verified against a test
   database instead (`tests/test_round7_review_fixes.py`), never against
   production. **Do not run suite 18** (the round-6 `verify_all`) against
   this build: it left its reported smoke team ownerless and so closed the
   rollback at once.

## Rollback

**Not a code-only rollback.** The previous build cannot start against a
database at `a1c4e0f2b7d9`: its Pre-Deploy `flask db upgrade` fails ("Can't
locate revision a1c4e0f2b7d9"), and with `STREAKFIT_ENFORCE_DB_HEAD=1` its
boot head check refuses. (The design note said "a code-only rollback is the
rollback"; with this repository's head check that is not true.) So, as for
`d4f1b6e2a9c3`, **the schema goes back first, using the new build, then the
old build is deployed**:

1. Check nothing is ownerless (read-only):
   `SELECT count(*) FROM team WHERE created_by_user_id IS NULL;`
2. If it is **0**, in the Render Shell of the running new build (the owner
   runs this):
   ```
   PGOPTIONS='-c lock_timeout=5s' flask db downgrade d4f1b6e2a9c3
   ```
   `SET NOT NULL` scans `team` under ACCESS EXCLUSIVE (1.4 s wall-clock for
   5,000 teams locally). On a lock timeout, run it again.
3. Deploy the previous build.

**If any team is ownerless, the downgrade refuses** (`RuntimeError: N
team(s) are ownerless ... Roll forward instead`), by design: making the
column NOT NULL again would need an owner nobody chose. Verified on
PostgreSQL 17.10 and SQLite: the refusal leaves the version at
`a1c4e0f2b7d9` and the column nullable. **Then the only rollback is rolling
forward** (a fixed build on the same schema).

### The rollback window, and what closes it

The downgrade rollback is open **exactly while step 1's count is 0**. It
opens when the deploy finishes and the post-deploy `verify_all` (suite 19)
does not close it. It closes the first time any of these happens in
production:

1. An owner leaves a team choosing "nobody". Today **every** owner who leaves
   a team that other people are in has to (every handover is refused).
2. A person deletes their account while a team they created, and already
   left, still has members and none of them is a verified adult (the
   fallback). Today that is every such team.
3. A sole owner leaves their team, or deletes their account, while a pending
   report or a legal hold keeps the team (P9; the D-R5-1 path). Nobody is
   told, by design, so this can happen with no visible sign.
4. `scripts/cleanup_qa_smoke.py --execute` deletes a `qa_smoke_` account
   that is the only member of a smoke team its pending reports keep. That is
   case 3 done by the tooling, and after every `verify_all` run the smoke
   team is in exactly that state. **Do not run the cleanup with `--execute`
   while the rollback may still be needed**, unless an operator has first
   closed the smoke team's reports.

It does **not** reopen by itself in practice: the cleanup deletes an
ownerless team only once it is empty and nothing keeps it, and a team with
members never gets an owner back (no M2). So **make the rollback decision
before real users can do 1-3**, i.e. right after the post-deploy checks, and
re-run step 1 immediately before any downgrade.

## What else ships with it (code, no schema)

- Handover (`POST /api/teams/<id>/owner`, leave with `new_owner_user_id`):
  400 `new_owner_not_eligible` unless the member is a verified adult with no
  social suspension.
- Leave with `no_owner: true` (P3).
- Lookup and join of an ownerless team: the generic invalid-code answers;
  `GET /api/teams/<id>` returns `ownerless`, `read_only`, `ownerless_notice`
  and `invite_code: null`.
- Read-only enforced in three layers (routes with a `FOR SHARE` team lock,
  the system writers, and a `before_flush` guard on every new content row).
- A suspended owner keeps the team but cannot remove members (P7).
- Account deletion: the fallback picks only verified, unsuspended adults,
  else the team becomes ownerless; a sole owner's team under a report or
  hold is kept ownerless and the deletion proceeds (P9; the
  `team_report_open` refusal is retired).
- Sign-up reserves the `qa_smoke_` prefix (review D-R5-3), and
  `scripts/cleanup_qa_smoke.py` deletes only accounts in the QA provenance
  ledger.
