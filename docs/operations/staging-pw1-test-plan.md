# Staging test plan: PW-1, its rollback, and Render's failed-deploy behaviour

**Status: plan only. No staging resource exists or is approved.** The D67
staging service was deleted on 2026-10-05. **Every step below needs the
owner's explicit go-ahead first,** because each one creates billable
resources or deploys.

- Production (`streakfit.pro`, `streakfit-api`, its Neon database) is
  **never** touched by this plan.
- Every account used is synthetic.

## What staging must answer

| # | Question | Why |
|---|---|---|
| Q1 | Does PW-1 deploy on Render as rehearsed locally? Expected: Pre-Deploy migration `d4f1b6e2a9c3 → e8b3c1f0a7d2`, head check passed, one worker | Local rehearsals use the same code, but not Render's Pre-Deploy or gunicorn `--preload` |
| Q2 | Does Rollback A (the compatibility build) switch with **no failed requests and no revoked session served**? | The preferred rollback must be proven on the real platform |
| **Q3** | **When a replacement instance fails to start, does Render keep the previous instance serving?** Both cases: (a) Pre-Deploy fails; (b) Pre-Deploy passes, but gunicorn exits (the head check) | The runbook's failure handling assumes it. Unverified for this service |
| Q4 | (Optional) How long is Rollback B's outage window, and does `/health` stay 200 through it? | Puts a number on the warning |

## S0: set up (owner only; the assistant never creates or sees credentials)

1. **Database:** a **new, empty** PostgreSQL 17 database for staging only.
   - For example, a new Neon project, or a new database in a non-production
     project.
   - **Not a branch of the production project**: a Neon branch copies
     production data.
   - Record its region.
2. **Render web service** `streakfit-staging-pw1`:
   - Starter plan;
   - same region as production;
   - Python `runtime.txt` 3.12.7;
   - from `mudmantim/streakfit-api`;
   - **Auto-Deploy Off**.

   **Commands copied from production's Render settings exactly**, not from
   `render.yaml`, which differs:
   - Pre-Deploy: `flask db upgrade`.
   - Start: `STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app`.

   Production runs gunicorn with `--preload`, apparently from Render's
   defaults rather than the Start Command (`streakfit_production_runtime`
   notes). **Confirm in the first staging boot log** that the head check runs
   once, in the master, before `Booting worker`. If it doesn't, match
   production's effective settings before testing Q3.
3. **Environment** (owner-entered in Render; new values, **never
   production's**):
   - `SECRET_KEY`, `JWT_SECRET_KEY`, `ADMIN_SECRET`;
   - `DATABASE_URL` = the staging database;
   - `ANTHROPIC_API_KEY` **unset** (Ask Rickie answers 503; nothing reaches
     the provider);
   - `STREAKFIT_AGE_POLICY` unset;
   - rate-limit storage: unset (in-memory) unless a staging Redis is approved.
4. **One synthetic account**, `staging_probe_<date>`, with a 15+ character
   password, created through the staging UI after S2. Its password goes in
   `STAGING_PROBE_PASSWORD` on the machine running the probe, never on a
   command line.
5. **Probe:** `scripts/staging_probe.py <staging https URL> --username staging_probe_<date>`.
   - It refuses `streakfit.pro` and plain http.
   - It writes one CSV line per second: build SHA, identity, `/health`, a real
     sign-in every 15 s, a live session, and a revoked session.

## S1: baseline (production parity)

1. Manual deploy **`1d7b2f2d48b36beccc21f09cfc88675cac11629a`**.
2. Expect Pre-Deploy to build the chain up to `d4f1b6e2a9c3`, then the head
   check to pass.
3. `verify_all` v15 against the staging URL: expect 214/214.

## S2: PW-1 (Q1)

1. Start the probe, without `--revoked-token-file`.
2. Deploy the PW-1 SHA (the tip of `feature/pw1-and-child-safety-phase0`).
3. **Expect in the deploy log:**
   - `Running upgrade d4f1b6e2a9c3 -> e8b3c1f0a7d2`;
   - `Database migration check passed (at head e8b3c1f0a7d2)`;
   - one worker.
4. **Expect in the probe:**
   - the SHA flips once;
   - **no status 0, no 5xx**;
   - sign-ins 200 throughout.
5. Run `verify_all` v18: expect 223/223.
6. **Create the revoked session for later steps:**
   - sign in as the probe account on device B and save that token to a file;
   - on device A, change the password in Settings;
   - the device-B token should now get 401 `session_ended`.
   - The probe account's new password goes into `STAGING_PROBE_PASSWORD`.

## S3: Rollback A (Q2)

1. Restart the probe with `--revoked-token-file <device-B token>`.
2. Deploy the compatibility SHA (the tip of `rollback/pw1-compat`).
3. **Expect in the deploy log:** **no** "Running upgrade", then the head check
   passing at `e8b3c1f0a7d2`.
4. **Expect in the probe:**
   - the SHA flips once;
   - **no status 0, no 5xx**;
   - sign-ins 200;
   - **the revoked session is 401 `session_ended` on every line**.
5. Run `verify_all` v15 (the compatibility branch's suite): expect 214/214.
6. Sign-up refuses a 14-character password (`too_short`).

## S4: Render's behaviour when the replacement fails (Q3)

The answer the runbook currently assumes. Two **staging-only** probe builds
are cut from the PW-1 SHA. They are **never pushed to `main`, never deployed
to production**, and deleted afterwards:

- **`staging/predeploy-fails`:** adds one migration file whose `upgrade()`
  raises.
  - Pre-Deploy fails.
- **`staging/boot-fails`:** `_assert_db_at_head()` always refuses.
  - The real head check, made to fail. Pre-Deploy runs without
    `STREAKFIT_ENFORCE_DB_HEAD`, so it passes, and gunicorn then exits 1.

**For each, with PW-1 serving and the probe running:**

1. Deploy the probe build.
2. Wait until Render marks the deploy failed, then 2 more minutes.
3. **Pass:**
   - the probe's SHA **never** changes from PW-1's;
   - **zero status-0 lines and zero 5xx**;
   - sign-ins keep returning 200;
   - the Render dashboard still shows the previous deploy as live.
4. **Fail:** any gap or any 5xx. The runbook's failure tables must then say
   "a failed deploy causes an outage", and Rollback A must be scheduled like
   a maintenance window.
5. Record the deploy IDs, the dashboard state and the probe CSV.

## S5: roll forward

1. Deploy the PW-1 SHA again.
2. **Expect:** no "Running upgrade"; the revoked session still 401; sessions
   issued under the compatibility build still 200.
3. A new password change revokes the other device again.

## S6: Rollback B window (optional, Q4)

**Only if the owner wants the number.** This is an outage by design, on
staging only.

1. With PW-1 serving and the probe running, from a trusted machine with the
   **staging** `DATABASE_URL` in the environment:

   ```
   PGOPTIONS='-c lock_timeout=5s' flask db downgrade d4f1b6e2a9c3
   ```

2. Then deploy `1d7b2f2`.
3. **Expect:**
   - sign-in 500s while `/health` stays 200;
   - the window ends when the `1d7b2f2` instance goes live.
4. Record its length.

## S7: teardown

1. Delete:
   - the staging Render service;
   - the staging database;
   - the probe branches (`staging/*`).
2. Confirm in Render and Neon billing that nothing staging-related remains.
3. Record the evidence in `e2e-campaign/evidence/` (CSV, deploy IDs, verify
   outputs), with no secrets.

## Exit criteria for a production release decision

- Q1–Q3 pass, and Q3 results are written into the PW-1 runbook.
- Both branches green on GitHub CI at the exact SHAs to be deployed.
- A fresh production backup (custody scripts) taken immediately before the
  release.
