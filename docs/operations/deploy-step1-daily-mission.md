# Step 1 — stored daily missions: what ships, how to deploy, how to roll back

D6/D50/D59 day-boundary work, Step 1 (mission identity; mission days stay
UTC dates). Built on production `bb2c9b0` (D49, Alembic head `c7d2a9e41b30`).
Adds Alembic revision **`d4f1b6e2a9c3`** (`daily_mission` table).
**Not deployed.** Deploying, and any rollback, need owner approval.

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

## Deploy sequence

Unchanged from every release: the Render Start Command
`flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app` applies the
migration once, before the new code serves.

- The migration only **creates** `daily_mission` (foreign key to `user`). It
  reads, changes and backfills nothing; history is untouched.
- `CREATE TABLE … REFERENCES "user"` takes a SHARE ROW EXCLUSIVE lock on
  `user` for the instant of the create; `lock_timeout = 5s` makes a stuck lock
  fail the deploy instead of queueing requests behind it. A failed upgrade
  stops the new build from starting; the old one keeps serving.
- Deploy outside the UTC midnight window (00:00Z ± 30 min), so the deploy and
  a day change do not coincide.

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

1. `/health` and `/admin` → build identity shows the new commit; database at
   head `d4f1b6e2a9c3`.
2. `verify_all` against production (explicit base URL). Suite version 14: the
   mission module checks the Step 1 contract — the stored five are stable
   across reads, `day_ends_in_s` is sane, an undated write asks for a refresh,
   a stale day is refused (409 `day_changed`) with nothing written, retries are
   idempotent, and the five-slot bonus is paid once. (Rehearsed 211/211 against
   a disposable local server with rate limiting on.)
3. Watch for `event=mission_slot_redrawn_missing_exercise` (should be zero:
   it means a deploy removed an exercise someone had today — see below).

## Exercise keys are now stored data

`daily_mission.slots` and `daily_completion.exercise_key` hold exercise keys.
Renaming or removing a key no longer crashes anyone's day (an unfinished slot
is redrawn; a completed one is shown without its picture), but
`tests/test_exercise_library_compat.py` fails until the removal is recorded in
`RETIRED` with a reason — removing a key is a deliberate decision.

## Rollback runbook

The previous build cannot start against a database at `d4f1b6e2a9c3`: its
`flask db upgrade` fails ("Can't locate revision d4f1b6e2a9c3"), and if that
were bypassed `STREAKFIT_ENFORCE_DB_HEAD=1` refuses to boot (`SystemExit(1)`).
So the schema goes back **first, with the Step 1 code**:

1. **Downgrade, using the Step 1 build** (Render shell on the running Step 1
   service, or a one-off job from the Step 1 commit, with production's
   `DATABASE_URL`):

   ```
   flask db downgrade c7d2a9e41b30
   ```

   One transaction; drops `daily_mission` only. Lost: today's stored fives,
   their Brain Boost snapshots, and harder-effort choices saved for tomorrow
   (`next_effort`). Kept: every completion, mission bonus, Brain Boost answer,
   effort choice, XP and acorn — the previous build reads all of them.

2. **Deploy the previous build (`bb2c9b0`).** Its `flask db upgrade` is now a
   no-op and the head check passes.

3. **Check:** `/health`, `/admin` (head `c7d2a9e41b30`), `verify_all`.

Effects while rolled back: the previous build recomputes the five per request
again (D59 returns); pages loaded from the Step 1 build keep working against
it (they send `mission_date`, which it ignores, and without `day_ends_in_s`
they simply do not schedule the day-end refresh).

**Never** deploy `bb2c9b0` before step 1: the deploy fails at
`flask db upgrade` and Render keeps the Step 1 build serving — safe, but not a
rollback.

**Roll forward:** redeploy the Step 1 build; its upgrade recreates an empty
`daily_mission`, and each person's mission is stored again on their next
request (a day already in progress adopts its completions).

Rehearsed on disposable PostgreSQL 16 (not production): see
`e2e-campaign/evidence/d6_d50_d59/raw/step1_remediation_2026-10-12/rollback/`.
