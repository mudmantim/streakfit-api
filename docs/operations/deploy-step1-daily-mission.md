# Step 1 — stored daily missions: what ships, how to deploy, how to roll back

D6/D50/D59 day-boundary work, Step 1 (mission identity; mission days stay
UTC dates). Built on production `bb2c9b0` (D49, Alembic head `c7d2a9e41b30`).
Adds Alembic revision **`d4f1b6e2a9c3`** (`daily_mission` table).

**Released 2026-10-12** at `6b6e1cd02a6a53ffc10ab1896187787ff1002163`
(Render deploy `dep-db645qlckfvc73c2l9j0`; application code identical to the
reviewed Step 1 commit `1b90084`, plus a test-only lint fix). Release record:
`e2e-campaign/evidence/d6_d50_d59/raw/step1_release_conditions_2026-10-12/PRODUCTION_RELEASE.md`.
Any rollback needs owner approval.

## What changes for people

- The day's five exercises and the Brain Boost question are stored on the
  first request of the day and never recomputed. Before the first exercise,
  skill and effort may change freely (the five are rebuilt); after it,
  completed exercises never move, an easier effort redraws only the unfinished
  ones, and a harder effort or new skill level starts the next day.
- The mission bonus is paid when all five stored exercises are done.
- Every completion and Brain Boost answer names its mission day. A tap for a
  day that has ended is refused (409 `day_changed`) and shown as "a new day
  has started", never credited to the next day. Retries of saved work are
  idempotent.
- The page learns how long the server's day has left and refreshes from that,
  never from the device clock.

## How production runs a deploy

Render service `streakfit-api` (`srv-d8f1hh6rnols73ak3a10`, `streakfit.pro`):

- **Auto-Deploy is Off.** Pushing to `main` deploys nothing; every release is
  a manual deploy of an exact commit.
- **Pre-Deploy Command: `flask db upgrade`.** Render runs it once per deploy,
  after the build and before the new instance starts. This is where
  migrations run. If it fails, the deploy stops and the build already live
  keeps serving.
- **Start Command:
  `STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app`.**
  It does not migrate. `STREAKFIT_ENFORCE_DB_HEAD=1` makes the app refuse to
  boot (`SystemExit(1)`) unless the database is stamped at this build's Alembic
  head; `STREAKFIT_RETENTION_SWEEPER=1` runs the retention sweep in-process;
  gunicorn runs one sync worker (Render's default `WEB_CONCURRENCY=1` on this
  plan). One instance, autoscaling off.

## Deploy sequence (as released)

1. **Preconditions**, each re-checked immediately before deploying:
   - Production `/api/build-identity` shows the expected current commit and
     migration head; the dashboard shows Auto-Deploy Off and 1 instance.
   - `main` is exactly the release commit and its CI run is green — and CI
     actually ran past lint (mypy, build gate, tests), not just "completed".
   - Time is outside 23:30–00:30 UTC (a deploy should not coincide with a
     mission-day change) and away from announced Render maintenance.
2. **Fresh backup and restore rehearsal (owner-run; the scripts ask for the
   owner's secrets).** In `~/backups/streakfit/`: `streakfit-backup.sh` takes
   an encrypted dump and proves it decrypts and parses; `streakfit-rehearse.sh`
   restores that dump into a disposable container and runs this release's
   migration chain against it (`SFRH_BEFORE=c7d2a9e41b30`,
   `SFRH_REPO=<release worktree at the release commit>`). **The deploy must
   start within 30 minutes of the backup finishing**; if that window passes,
   take a new backup and rehearse again. The backup's own deletion deadline is
   printed by the script — see `backup-retention-deadline.md`.
3. **Manual deploy of the exact commit:** Render → `streakfit-api` → Manual
   Deploy → Deploy a specific commit → paste the **full 40-character SHA** →
   select the single matching commit → Deploy Commit. Record the deploy ID
   (`dep-…`).
4. **Watch the deploy log:** the build checks out the exact SHA; Pre-Deploy
   logs `Running upgrade c7d2a9e41b30 -> d4f1b6e2a9c3`; startup logs
   `Database migration check passed (at head d4f1b6e2a9c3)`,
   `Using worker: sync` and **one** `Booting worker`; status Deploy succeeded.

What the migration does:

- It only **creates** `daily_mission` (foreign key to `user`). It reads,
  changes and backfills nothing; history is untouched.
- `CREATE TABLE … REFERENCES "user"` takes a SHARE ROW EXCLUSIVE lock on
  `user` for the instant of the create; `lock_timeout = 5s` makes a stuck lock
  fail the Pre-Deploy instead of queueing requests behind it.

### Pages open across the deploy

A page loaded before the deploy runs the old JavaScript until it reloads. It
sends completions and Brain Boost answers without a mission day; the server
refuses them (400 `mission_date_required`, `refresh_required: true`) and
writes nothing. The old page cannot be told to reload: it shows its generic
"Not saved — tap to try again" until the person reloads or reopens the app
(the HTML is network-only and the app-shell assets are content-versioned, so
any reload gets the new code). From this release on, the page itself shows a
"StreakFit has been updated — Refresh" bar whenever the server answers
`refresh_required`, so future changes to the request contract prompt a reload.

### Days already in progress at the deploy

The first request of the day stores exactly the five the old code was
showing, with that day's completions ticked. One rare case shows differently:
someone who switched skill tier mid-day under the old code may have five
completions spread over four categories. The old code paid that day's bonus
(any five rows) and the streak counts it; the stored mission fills four of its
five slots, so the page shows 4/5. Finishing the fifth pays nothing twice.
Decided: no compatibility fix — showing 5/5 would misstate the stored five,
and nothing is lost. Pinned by
`test_a_legacy_day_paid_on_five_rows_shows_its_slots_and_is_never_paid_again`.

Brain Boost answers from before this release keep their stored result; a
retry for such a day returns it without an answer key (`question_available:
false`), because the question shown that day was not stored and is never
reconstructed from today's library.

## After deploy

Read-only checks (as done for this release):

1. `/api/build-identity`: `gitSha` = the release commit, `migration.latest`
   `d4f1b6e2a9c3`, `atHead: true`, `appliedCount` 28.
2. `/health`: HTTP 200, `{"status":"ok"}`.
3. Topology: one instance (Manual Scaling 1), one sync worker (a single
   `Booting worker` line).
4. Application logs since go-live: search for `Traceback` and for 5xx
   statuses, with a positive control (a search for status 200 must find
   requests, or an empty 5xx result means nothing).
5. Watch for `event=mission_slot_redrawn_missing_exercise` (should be zero:
   it means a deploy removed an exercise someone had today — see below).

Owner-approved only:

6. One disposable `qa_smoke_*` account, created, exercised and deleted by an
   owner-run script (for this release: 14/14 — see the release record).
7. `verify_all` against production (explicit base URL; suite v14 includes the
   Step 1 contract). It creates throwaway accounts, so it is not run without
   approval. (Rehearsed 211/211 against a disposable local server.)

## Exercise keys are now stored data

`daily_mission.slots` and `daily_completion.exercise_key` hold exercise keys.
Renaming or removing a key no longer crashes anyone's day (an unfinished slot
is redrawn; a completed one is shown without its picture), but
`tests/test_exercise_library_compat.py` fails until the removal is recorded in
`RETIRED` with a reason — removing a key is a deliberate decision.

## Rollback runbook

The previous build cannot start against a database at `d4f1b6e2a9c3`: its
Pre-Deploy `flask db upgrade` fails ("Can't locate revision d4f1b6e2a9c3"),
and if that were bypassed `STREAKFIT_ENFORCE_DB_HEAD=1` refuses to boot. So
**the schema goes back first, with the Step 1 code, then the old build is
deployed.** Never the other way round.

**Expect a short interruption.** Between step 2 (the downgrade) and step 3
finishing (the old build live), the Step 1 build is still serving against a
database without `daily_mission`: `GET /api/daily`, completions, effort and
Brain Boost answers return errors, while `/health` and the rest of the app
keep working. It lasts as long as the old build's deploy (a few minutes; the
Step 1 build took about 80 seconds from click to live). Nothing is written
wrongly during it — the failing routes write nothing.

1. **Decide on Maintenance Mode (owner).** Render → `streakfit-api` →
   Settings → Maintenance Mode → enable, so visitors see Render's maintenance
   page instead of errors for the interruption above. It is a settings change:
   enable it only with the owner's approval, and remember it also hides
   `/health` and `/api/build-identity` from outside checks until it is turned
   off. Without it, the interruption is visible as failed mission requests.
2. **Downgrade, using the Step 1 build.** In the Render Shell on the running
   Step 1 service (the owner runs this; assistants are not given the shell),
   with production's `DATABASE_URL` already in the environment:

   ```
   flask db downgrade c7d2a9e41b30
   ```

   One transaction; drops `daily_mission` only. Lost: today's stored fives,
   their Brain Boost snapshots, and harder-effort choices saved for tomorrow
   (`next_effort`). Kept: every completion, mission bonus, Brain Boost answer,
   effort choice, XP and acorn — the previous build reads all of them.
   Confirm the output ends with `Running downgrade d4f1b6e2a9c3 -> c7d2a9e41b30`.
3. **Deploy the previous build:** Manual Deploy → Deploy a specific commit →
   `bb2c9b04a61fe26e51b2a33efaf6807d326f2eeb`. Its Pre-Deploy upgrade is now a
   no-op and the head check passes.
4. **Recovery checks:** `/api/build-identity` shows `bb2c9b04a61f` and
   `c7d2a9e41b30` with `atHead: true`; `/health` 200; one `Booting worker`;
   no `Traceback`/5xx after the old build is live. Then turn Maintenance Mode
   off if it was enabled, and re-check `/health` from outside.

Effects while rolled back: the previous build recomputes the five per request
again (D59 returns); pages loaded from the Step 1 build keep working against
it (they send `mission_date`, which it ignores, and without `day_ends_in_s`
they simply do not schedule the day-end refresh).

**If the old build is deployed first by mistake:** its Pre-Deploy fails and
Render keeps the Step 1 build serving — safe, but not a rollback. Start again
at step 1.

**Roll forward:** redeploy the Step 1 build; its Pre-Deploy recreates an empty
`daily_mission`, and each person's mission is stored again on their next
request (a day already in progress adopts its completions).

Rehearsed on disposable PostgreSQL 16 (not production): see
`e2e-campaign/evidence/d6_d50_d59/raw/step1_remediation_2026-10-12/rollback/`
and `.../step1_release_conditions_2026-10-12/rollback_rehearsal_final.json`.
The rehearsal ran the steps in sequence; it did not measure the interruption
window, which is estimated from the release's own deploy time.
