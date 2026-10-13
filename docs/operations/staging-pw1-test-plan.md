# Staging test plan: PW-1, its rollback, and Render's failed-deploy behaviour

**Status: approved in principle (2026-10-13). No staging resource exists.**
Resource creation needs the owner's separate approval after reviewing the
pricing and configuration in their own accounts. The D67 staging service
was deleted on 2026-10-05.

- Production (`streakfit.pro`, `streakfit-api`, its Neon project, its Key
  Value, its secrets and environment groups) is **never** touched, read
  from or reused by this plan.
- Every account used is synthetic, and the staging database only ever holds
  data this plan's tests create.
- `STREAKFIT_AGE_POLICY` stays `off` throughout.
- **Budget:** one Render Starter web service ($7/month, billed by the
  second; expected well under $1 for this round). Key Value Free and Neon
  Free are $0. **Ceiling for this round: $7.**

## What staging must answer

| # | Question | Why |
|---|---|---|
| Q1 | Does PW-1 deploy on Render as rehearsed locally? Expected: Pre-Deploy migration `d4f1b6e2a9c3 → e8b3c1f0a7d2`, head check passed, one worker | Local rehearsals use the same code, but not Render's Pre-Deploy or gunicorn `--preload` |
| Q2 | Does Rollback A (the compatibility build) switch with **no failed requests and no revoked session served**? | The preferred rollback must be proven on the real platform |
| **Q3** | **When a replacement instance fails to start, does Render keep the previous instance serving?** Both cases: (a) Pre-Deploy fails; (b) Pre-Deploy passes, but gunicorn exits (the head check) | The runbook's failure handling assumes it. Unverified for this service |

Rollback B's outage window (a database downgrade, then a redeploy) is
**excluded from this round** by the owner. Nothing in this plan runs
`flask db downgrade`.

**Execution order:** S0 → S1 → S2 → **S3 (failed deploys, with PW-1 live)**
→ S4 (Rollback A) → S5 (roll forward) → S6 (teardown). S3 comes straight
after S2 so that the failures are tested against the build that will be
serving in production when a real deploy fails.

## Separation rules (apply to every step)

1. **Database:** a **new Neon project**, never a branch of, or a database
   inside, the production project, and never the second, unidentified
   `streakfit` project (`deploy-runbook.md` §3.1). No `pg_dump`,
   `pg_restore`, import or copy of any production data, ever.
2. **Redis:** a new staging Key Value. Never production's.
3. **Secrets:** `SECRET_KEY`, `JWT_SECRET_KEY` and `ADMIN_SECRET` are created
   with Render's **Generate** button. Nobody types, sees or copies them, and
   no value is pasted from production.
4. **Environment groups:** none are linked to the staging service.
5. **Tokens:** a test session token is only ever held in a file that
   `scripts/staging_probe.py --issue-token-file` creates. That file is new,
   owner-only (`0600`) and in a private temporary directory, and the probe
   refuses any token file others can read. The directory is deleted in S6.
   Tokens are never pasted into chat, a command line or a URL.
6. **Assistant:** sees no credentials. The owner creates every resource,
   enters every value and clicks every deploy. The assistant reads logs,
   probe output and `verify_all` results.

## S0: set up (owner only)

### S0.1 Before creating anything

- [ ] On GitHub, confirm the SHAs this round will deploy, and record them:
  - `main` = `1d7b2f2d48b36beccc21f09cfc88675cac11629a` (production's build);
  - **PW-1** = the tip of `feature/pw1-and-child-safety-phase0`, green on CI;
  - **compat** = the tip of `rollback/pw1-compat`, green on CI;
  - `staging/predeploy-fails` and `staging/boot-fails`, each one commit on
    top of the PW-1 tip.
- [ ] In Render, open **`streakfit-api` → Settings** and note these
      **non-secret** fields, to copy into staging: Build Command, Start
      Command, Pre-Deploy Command, Health Check Path, Region. Do not open its
      Environment page in this step.

### S0.2 Neon: a new, empty project

1. Go to console.neon.tech and click **New project**.
2. Fill in:
   - **Project name:** `streakfit-staging-pw1`;
   - **Postgres version:** 17;
   - **Cloud provider / region:** AWS, **US East 2 (Ohio)**, the same as
     `streakfit-api`;
   - leave any optional add-ons (such as Neon Auth) off.
3. Click **Create project**.

   The new project's default branch may be called `production`. **That is
   the staging project's own branch, not your production.** Every later step
   names the project `streakfit-staging-pw1`.
4. **Record the staging endpoint ID.** Open **Branches → (the default
   branch) → Computes** and read the endpoint ID, `ep-…`. It is not a
   secret. Teardown matches against it.
5. **Copy the connection string, straight into Render (S0.4):**
   - click **Connect**;
   - choose database `neondb` and role `neondb_owner`;
   - set **Connection pooling** to match production's `DATABASE_URL`. Check
     only whether production's host contains `-pooler`.

   Never paste it into chat, a file or a command line.

### S0.3 Render: a new Key Value (Free)

1. Go to dashboard.render.com and click **+ New → Key Value**.
2. Fill in:
   - **Name:** `streakfit-staging-pw1-kv`;
   - **Project:** none, or a new staging one. **Not production's.**
   - **Region:** **Ohio**, the same as the web service;
   - **Maxmemory policy:** the same as production's Key Value (a
     non-secret setting on its Info page);
   - **Instance type:** **Free**.
3. Click **Create Key Value**.
4. On its page, open **Connect → Internal** and copy the **Internal Key
   Value URL** straight into Render (S0.4).

### S0.4 Render: the web service (Starter)

Creating the service **starts its first deploy immediately**, from the
branch chosen below. Choosing `main` makes that first deploy test S1.

1. Click **+ New → Web Service**, then **Git Provider →
   `mudmantim/streakfit-api` → Connect**.
2. Fill in:

   | Field | Value |
   |---|---|
   | Name | `streakfit-staging-pw1` |
   | Project | none, or the staging one. **Not production's** |
   | Language | Python 3 (the version comes from `runtime.txt`, 3.12.7) |
   | Branch | `main` (it must still be `1d7b2f2`) |
   | Region | **Ohio** |
   | Root Directory | as production (blank) |
   | Build Command | **copied from production** (S0.1) |
   | Start Command | `STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app`, exactly as production's dashboard shows it (`render.yaml` differs; ignore it) |
   | Instance Type | **Starter** ($7/month). Check the price on this screen |

3. **Environment Variables.** Add each one with **Add Environment
   Variable**. Do **not** use "Add from .env" and do **not** link an
   environment group.

   | Key | Value |
   |---|---|
   | `SECRET_KEY` | click **Generate** |
   | `JWT_SECRET_KEY` | click **Generate** |
   | `ADMIN_SECRET` | click **Generate** |
   | `DATABASE_URL` | the staging Neon string (S0.2) |
   | `RATELIMIT_STORAGE_URI` | the staging internal Key Value URL (S0.3) |
   | `STREAKFIT_AGE_POLICY` | `off` |

   **Leave unset.** Production may set some of these; staging must not:
   - `ANTHROPIC_API_KEY`: Ask Rickie answers 503, so nothing reaches the
     provider;
   - `RESEND_API_KEY`, `STREAKFIT_NOTIFY_*`, `STREAKFIT_PUBLIC_URL`: no
     email is sent;
   - `STREAKFIT_EVIDENCE_KEY`: no photo evidence is captured;
   - `WEB_CONCURRENCY`, `GUNICORN_CMD_ARGS`.

4. **Advanced:**
   - **Health Check Path:** as production;
   - **Pre-Deploy Command:** `flask db upgrade`;
   - **Auto-Deploy:** **Off**;
   - no disk, no secret files.
5. Click **Deploy Web Service**, then continue with S1.

### S0.5 Separation check (owner; compare hosts only, never whole values)

Each check is a mismatch you should see. In Render → Environment, each
service's variable can be revealed and the part after the final `@` read.

- [ ] The staging `DATABASE_URL` host ≠ production's. The endpoint ID is the
      one recorded in S0.2.
- [ ] The staging `RATELIMIT_STORAGE_URI` host (`red-…`) ≠ production's.
- [ ] Staging → Environment shows **no linked environment group**.
- [ ] The three secrets were generated, not pasted.

### S0.6 Local probe setup (owner's machine)

```
umask 077
STAGE=$(mktemp -d)        # private, deleted in S6
```

Probe CSVs and token files live in `$STAGE` until S6. CSVs contain no
secrets and are copied into the evidence folder.

## S1: baseline (production parity)

1. The service's first deploy, from `main` = `1d7b2f2`.
2. **Expect in the deploy and boot log:**
   - Pre-Deploy builds the chain from empty up to `d4f1b6e2a9c3`;
   - `Database migration check passed (at head d4f1b6e2a9c3)`;
   - **the app imports before `Booting worker`**, `Using worker: sync`, and
     exactly **one** `Booting worker`.

   If there is more than one worker, or no preload, stop. Match production's
   effective settings first.
3. Run `verify_all` v15 against the staging URL **passed explicitly**,
   because it defaults to production:
   `python scripts/verify_all.py https://<staging>.onrender.com`.
   Expect 214/214.
4. **Optional:** reveal the staging `ADMIN_SECRET` in Render, open
   `/admin` → System Health, and confirm:
   - `child_safety.age_policy` PASS (off);
   - the three `ratelimit.*` checks PASS against the staging Key Value.

## S2: PW-1 (Q1)

1. In the staging UI, create the synthetic account `staging_probe_<date>`
   with a 15+ character password. Put the password in your shell only:
   `read -rs STAGING_PROBE_PASSWORD; export STAGING_PROBE_PASSWORD`.
2. Start the probe:
   `python -I scripts/staging_probe.py <url> --username staging_probe_<date> > $STAGE/s2.csv`.
3. Set the service **Branch** to `feature/pw1-and-child-safety-phase0`,
   then **Manual Deploy → Deploy a specific commit → the PW-1 SHA**.
4. **Expect in the deploy log:**
   - `Running upgrade d4f1b6e2a9c3 -> e8b3c1f0a7d2`;
   - `Database migration check passed (at head e8b3c1f0a7d2)`;
   - one worker.
5. **Expect in the probe:**
   - the SHA flips once;
   - **no status 0, no 5xx**;
   - sign-ins 200 throughout.
6. Run `verify_all` v18: expect 223/223.
7. **Create the revoked session:**
   - `python -I scripts/staging_probe.py <url> --username staging_probe_<date> --issue-token-file $STAGE/device-b.token`.
     This is "device B". The file is new and `0600`.
   - In the browser, as the probe account, change the password in Settings.
   - `read -rs` the new password into `STAGING_PROBE_PASSWORD`.
   - The device-B token should now get 401 `session_ended`. The next step
     checks this.

## S3: Render's behaviour when the replacement fails (Q3)

Two **staging-only** builds, each one commit on the PW-1 SHA. They are
**never merged, never deployed to production**, and deleted in S6.

- **`staging/predeploy-fails`:** adds migration `f0a1staging0`, whose
  `upgrade()` raises before any schema change.
  - Pre-Deploy fails; the database stays at `e8b3c1f0a7d2`.
- **`staging/boot-fails`:** `_assert_db_at_head()` always refuses.
  - The real head check, made to fail. Pre-Deploy runs without
    `STREAKFIT_ENFORCE_DB_HEAD`, so it passes with nothing to do, and the
    gunicorn master then exits 1.

**For each build, in turn:**

1. **Confirm the known-good build is live.**
   - Render shows the PW-1 deploy as **Live**.
   - The probe's baseline guard must accept it:

     ```
     python -I scripts/staging_probe.py <url> --username staging_probe_<date> \
         --baseline-sha <PW-1 SHA> --revoked-token-file $STAGE/device-b.token \
         --seconds 1200 > $STAGE/s3-<build>.csv
     ```

     It exits 3 at once if any other build is answering. Then it watches
     for 20 minutes.
   - Let it run at least 60 s before deploying.
2. Set the Branch to the failure build, then **Manual Deploy → that
   build's SHA**.
3. Wait until Render marks the deploy **failed**, then 2 more minutes, and
   let the probe finish.
4. **Pass:**
   - the probe exits **0**: every round was answered by the PW-1 SHA, with no
     gap, no 5xx, and the device-B session `401 session_ended`
     throughout;
   - the Render dashboard still shows the PW-1 deploy as live.
5. **Fail:** exit 1, or a dashboard change.
   - The runbook's failure tables must then say "a failed deploy causes an
     outage", and Rollback A must be scheduled like a maintenance window.
   - Stop the round and redeploy the PW-1 SHA.
6. Record the deploy IDs, the dashboard state, the probe's summary line and
   the CSV.

Repeat from step 1 for the second build, so that it too is compared
against the build serving immediately before it.

## S4: Rollback A (Q2)

1. Restart the probe with `--revoked-token-file $STAGE/device-b.token`
   (no baseline: the SHA is meant to change).
2. Set the Branch to `rollback/pw1-compat`, then deploy the compat SHA.
3. **Expect in the deploy log:**
   - **no** "Running upgrade";
   - the head check passing at `e8b3c1f0a7d2`.
4. **Expect in the probe:**
   - the SHA flips once;
   - **no status 0, no 5xx**;
   - sign-ins 200;
   - **the revoked session is 401 `session_ended` on every line**.
5. Run `verify_all` v15 (the compatibility branch's suite): expect 214/214.
6. Sign-up refuses a 14-character password (`too_short`).

## S5: roll forward

1. Start the probe with `--revoked-token-file $STAGE/device-b.token` and no
   baseline, because the SHA is meant to change. Then deploy the PW-1 SHA.

   Read the CSV for:
   - one flip;
   - no gap and no 5xx;
   - the revoked session still 401 throughout.
2. **Expect in the log:** no "Running upgrade".
3. Sign in under the compat build before the flip; that session still
   answers 200 after it.
4. A new password change revokes the other device again.

## S6: teardown (owner)

1. **Render:** delete the web service `streakfit-staging-pw1` (Settings →
   Delete Web Service) and the Key Value `streakfit-staging-pw1-kv` (Info →
   Delete).
2. **Neon:** open the project, check that **its endpoint ID matches the one
   recorded in S0.2**, then Settings → Delete project. Never delete by name
   or region alone.
3. **GitHub:** delete `staging/predeploy-fails` and `staging/boot-fails` on
   the remote and locally.
4. **Local:**
   - `rm -rf "$STAGE"`, after copying the CSVs out;
   - `unset STAGING_PROBE_PASSWORD`.
5. **Billing:** confirm Render lists no staging service or Key Value, and
   that Neon lists only the original two `streakfit` projects.
6. **Evidence:** record it in `e2e-campaign/evidence/` with no secrets: the
   CSVs, deploy IDs, `verify_all` outputs and probe summaries. Write the Q3
   result into `deploy-pw1-password-change.md`.

## Exit criteria for a production release decision

- Q1–Q3 pass, and the Q3 results are written into the PW-1 runbook.
- Both branches green on GitHub CI at the exact SHAs to be deployed.
- A fresh production backup (custody scripts) taken immediately before the
  release.
