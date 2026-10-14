# Staging test plan: PW-1, its rollback, and Render's failed-deploy behaviour

**Status (revised 2026-10-13, after the staging incident): resources exist
and are SUSPENDED. Read "Resuming after the 2026-10-13 incident" first.**
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

## Resuming after the 2026-10-13 incident (read first)

On 2026-10-13 the staging service was created with **production's**
`DATABASE_URL` and ran against the production database for about 6.5 hours.
No user data was damaged (incident report:
`e2e-campaign/evidence/d6_d50_d59/INCIDENT_STAGING_ON_PRODUCTION_DB_2026-10-13.md`).
The root cause was a copied connection string, and every check that should
have caught it was a person reading a hostname. This section replaces those
checks with ones the code performs.

### Current state (verified 2026-10-13)

| Resource | State |
|---|---|
| Render `streakfit-staging-pw1` (`srv-db72…`) | **Suspended**. Environment: `SECRET_KEY`, `JWT_SECRET_KEY`, `ADMIN_SECRET` (generated), `STREAKFIT_AGE_POLICY=off`. **No `DATABASE_URL`** (removed, Phase A). |
| Neon `streakfit-staging-pw1` (`<staging endpoint>`: the ID recorded at S0.2 in the private evidence file, not in this public repo) | Empty: no tables. Never used. |
| Render Key Value for staging | **Not created** (one Free Key Value per workspace; option A). `RATELIMIT_STORAGE_URI` stays unset, so shared Redis rate limiting is **untested** in this round. |

### Why the order matters: the guard is only in builds that contain it

`db_guard.py` (PR #10, `safety/db-environment-guards`) refuses a staging
service on the production database (rule R2, by Render service ID), and it
proves the server's identity at boot (R7). **The builds this plan tests —
`1d7b2f2`, PW-1 `434b0f6`, `rollback/pw1-compat` `e4dcb4f` and the two
`staging/*` failure branches — do not contain it.** Setting
`STREAKFIT_DEPLOYMENT=staging` is harmless for them (they ignore it) and
required for the guard build.

So the round starts by deploying the **guard build** once (S0.7). That
proves, by the code and the server's own account, that this service's
environment is bound to the staging database. Every later deploy reuses
**the same environment, unchanged**. **Any edit to staging's environment
after S0.7 repeats S0.7 before the next deploy.**

### Environment prerequisites (complete list)

| Key | Value | Why |
|---|---|---|
| `DATABASE_URL` | built from **Neon → project `streakfit-staging-pw1` → Connect** (direct, database `neondb`, role `neondb_owner`). Never copied from anywhere else | the incident |
| `STREAKFIT_DEPLOYMENT` | `staging` | the guard build refuses to start without it (R4); it also makes `/api/build-identity` report `staging` |
| `SECRET_KEY`, `JWT_SECRET_KEY`, `ADMIN_SECRET` | already generated (keep) | staging's own, never production's |
| `STREAKFIT_AGE_POLICY` | `off` | unchanged |
| `RATELIMIT_STORAGE_URI` | **unset** | option A; the `ratelimit.shared_storage` self-check FAILs (expected) |
| `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `STREAKFIT_NOTIFY_*`, `STREAKFIT_PUBLIC_URL`, `STREAKFIT_EVIDENCE_KEY`, `MIGRATION_DATABASE_URL`, `STREAKFIT_PRODUCTION_DB_ENDPOINT`, `STREAKFIT_ALLOW_PRODUCTION_DB_FROM_HERE`, `WEB_CONCURRENCY`, `GUNICORN_CMD_ARGS` | **unset** | nothing reaches a provider; no override exists that could point staging at production |

**Start Command for S0.7:** `STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app`,
**without** `STREAKFIT_RETENTION_SWEEPER=1`. Nothing sweeps until the
database is proven. Add the sweeper flag back only after S0.7 passes.

### S0.7 Guard build smoke (before S1; owner deploys, Claude reads)

1. **Staging SQL Editor, read-only.**
   `select current_setting('neon.endpoint_id', true)` must return
   `<staging endpoint>`. Expect **no tables**.
2. **Render.** Set the environment exactly as the table above. Set the service **Branch** to
   `safety/db-environment-guards` (PR #10's tip, which includes `scripts/render_env_probe.py`). For this deploy only, set:
   - **Pre-Deploy Command:** `python scripts/render_env_probe.py pre-deploy && flask db upgrade`
   - **Start Command:** `python scripts/render_env_probe.py runtime && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app`

   The probe prints only Render's own identifiers, the *presence* of the database variables, and the endpoint ID; never a secret or the URL. It always exits 0, so it cannot change the deploy's outcome.
3. **Resume and deploy.** Expect in the log, in this order:
   - **The Pre-Deploy probe block (`render_env_probe phase=pre-deploy`). This is the specific test of `RENDER_SERVICE_ID` during Pre-Deploy.** Record, verbatim:
     - `RENDER_SERVICE_ID=srv-…` or `RENDER_SERVICE_ID: MISSING`;
     - `RENDER=…`;
     - the final line `production-in-this-phase: PASS|FAIL (…)`. That line is db_guard's own answer to "would production's Pre-Deploy be accepted with this phase's environment?"
   - Pre-Deploy: `Migrating database: deployment=staging endpoint=<staging endpoint> hosted=True service=srv-…`. Its `service=` must agree with the probe.
   - the migration chain from empty to `d4f1b6e2a9c3` (`Running upgrade …` lines, if Render keeps them);
   - the runtime probe block (`phase=runtime`): `RENDER_SERVICE_ID` should match the Pre-Deploy one, and `production-in-this-phase: PASS`;
   - boot: `Database identity verified (deployment=staging endpoint=<staging endpoint> … service=srv-…, server endpoint <staging endpoint>)`, then `Database migration check passed`.
4. **`/api/build-identity`** reports `"environment": "staging"`.
5. **Negative test (the incident replayed safely).**
   - Temporarily replace `DATABASE_URL` with a **dummy** string that names production's endpoint, with a made-up password: `postgresql://nobody:not-a-password@ep-odd-star-ajye3lag.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require`.
   - Deploy. Expect Pre-Deploy to fail at import with `Refusing to start: R2 … may not use the production database`, **before any connection** (no real credential exists, so nothing could connect anyway).
   - Restore the real staging URL, then repeat step 3's checks.
6. **Missing URL.** Remove `DATABASE_URL` and deploy. Expect `R1 … refusing the SQLite fallback`. Restore it.
7. Restore the plain commands: Pre-Deploy `flask db upgrade`; Start `STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app`. Adding the sweeper flag back happens only now.
   **From here the environment must not change.**

Pass criteria: every expectation above. If the Pre-Deploy probe says
`RENDER_SERVICE_ID: MISSING` / `production-in-this-phase: FAIL`, **stop**.
Production is not at risk: the guard build was never deployed there. The guard release cannot go to production until
`RENDER_SERVICE_ID` handling is revised (R2 would refuse production's own
Pre-Deploy).

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
   | `DATABASE_URL` | the staging Neon string (S0.2), from the **staging** project's Connect dialog only |
   | `STREAKFIT_DEPLOYMENT` | `staging` (added 2026-10-13; see "Resuming after the 2026-10-13 incident") |
   | `RATELIMIT_STORAGE_URI` | the staging internal Key Value URL (S0.3). **Superseded:** unset under option A |
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
- [ ] **(2026-10-13)** Its host's **first label is exactly**
      `<staging endpoint>` or `<staging endpoint>-pooler`. Its
      **database** is `neondb`, and its **role** is the staging project's
      own. Reading "contains the endpoint" was not enough: Check 1 passed on
      the wrong URL.
- [ ] **(2026-10-13)** Superseded as the deciding check by S0.7, where the
      code and the server confirm it. This list stays as a first look.
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
   - Pre-Deploy builds the chain from empty up to `d4f1b6e2a9c3`. **Since
     2026-10-13, S0.7 has already built it, so expect Pre-Deploy to report
     nothing to do.** Same head;
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
    gunicorn master then exits 1. Its log line reads "at revision
    'e8b3c1f0a7d2' but the code expects head 'e8b3c1f0a7d2'": the check is
    forced, so that is expected.
  - **This models production only under `--preload`** (S1 step 2).
    Rehearsed locally on 2026-10-13: with `--preload` the master exits 1.
    Without it, the worker exits 1, which gunicorn does not treat as a boot
    error, so the master respawns workers forever while holding the port. If
    S1 showed no preload, Q3b is testing a different failure.

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
