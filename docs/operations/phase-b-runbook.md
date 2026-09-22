# Phase B — controlled deployment runbook

**Status:** PREPARED, NOT AUTHORIZED. Nothing in this file has been executed.
**Commit to deploy:** `4b95bd1cd855e7a9f79cfc781316a54f5a52ddfe` (see STOP/GO).
**Prepared:** 2026-09-22.

---

## 0. Platform facts — verified, and what is not

**Scale-to-zero: NOT the mechanism.** Render's scaling documentation describes
manual scaling "to any fixed number of instances, up to a maximum of 100" and
never offers zero for a paid service. My earlier recommendation said "scale to
zero"; that was wrong and is withdrawn.

**Suspend/Resume IS supported** — dashboard and API
(`POST /services/{id}/suspend`, `/resume`). Render's own wording: *"Paid
instances (Starter and above) run continuously unless you suspend them."*

**NOT verified, and treated as unknown rather than assumed:**

| Question | Status |
|---|---|
| Does suspend drain in-flight requests or kill them? | **undocumented** |
| Does resume restart the existing build or redeploy? | **undocumented** |
| Is a suspended paid service billed? | **undocumented** |

Billing is the least of these: suspending cannot *add* cost, so the "no
charges" constraint is not at risk either way. The other two shape step 5 and
are called out there.

**The safety property that does not depend on any of it.** Once the schema is
migrated, the old build **cannot start**: `STREAKFIT_ENFORCE_DB_HEAD=1` calls
`SystemExit(1)` when the database revision is not that build's head
(`q1r2s3t4u5v6` vs `47f7dc9962e3`). So even if suspend's semantics are worse
than hoped, an old instance cannot come back and serve against the new schema.
It fails closed. What suspend must achieve is only the termination of the
*already-running* process, which does not re-check the guard.

## 0b. Two options, and what each actually costs

Presented honestly, because the safer one is not the cheaper one.

| | **A — suspend first (recommended)** | **B — deploy as configured** |
|---|---|---|
| Incompatible-schema window | **none** | ~30–90s |
| Total unavailability | **~8–15 min** | ~30–90s, 3 routes only |
| What breaks in the window | nothing — everything is down | `POST /api/coach`, `DELETE /api/coach/memory`, `POST /api/teams/<id>/messages` |
| Reasoning required | none | "unlikely anyone is mid-conversation" |

**B is less total downtime.** A is more downtime and *no* probabilistic
reasoning. You rejected leaning on low traffic, so this runbook is written for
**A**; B remains available and is a legitimate choice if you would rather have
90 seconds of partial failure than 12 minutes of planned outage.

---

## Stage 1 — Fresh backup, integrity check, Coach Notes count

Immediately before the migration. The verdict is valid **30 minutes**; past
that, run it again.

```bash
read -rsp 'Paste Neon DIRECT connection string (hidden): ' PGURL; echo
export PGURL
~/backups/streakfit/streakfit-premigration.sh
unset PGURL
```

Does three things in one run, from one consistent moment: a fresh
`pg_dump -Fc` streamed straight into `gpg --symmetric` (AES-256, no plaintext
on disk), proof it decrypts and `pg_restore` parses it, and the count of what
the destructive migration will discard. **Stops at the first failure and
refuses to report ready** — all six failure paths exercised.

**GO only if** it prints `READY TO MIGRATE` and `coach_note rows: 0`.

**If coach_note rows > 0:** STOP. The assumption that migration
`t4u5v6w7x8y9`'s `DELETE FROM coach_note` discards nothing no longer holds.
That is an owner decision, not a deploy-time judgement call.

## Stage 2 — Confirm the Neon snapshot

Owner-verified 2026-09-21: branch `production`, created 18:07:41 UTC,
32.9 MB, **expiry never**. Re-confirm it is still listed in the Neon console.

You now hold three recovery points: this snapshot, the re-wrapped encrypted
backup, and Stage 1's fresh one.

## Stage 3 — Stop the old application

```
Render Dashboard → streakfit-api → Settings → Suspend Service
```

Then confirm from outside — do not take the dashboard's word:

```bash
curl -si https://streakfit.pro/health | head -1     # expect failure/503, NOT 200
```

**This is the step that closes the window.** It stops the serving process, and
with it any in-flight request and any background thread inside gunicorn.

Note the deployed build has **no** background worker — `STREAKFIT_RETENTION_SWEEPER`
appears zero times in `fa92abd` — so there is nothing sweeping or writing on a
timer today. The threads this stops are only request handlers.

**Do not proceed until `/health` stops answering 200.** If it keeps
answering, suspension has not taken effect and the window is still open.

## Stage 4 — Run the 12 migrations with nothing serving

The service is suspended, so run them the same way the rehearsal ran — from a
machine with the production connection string:

```bash
cd ~/Desktop/Streakfit/integrate-product-completion
read -rsp 'Paste Neon DIRECT connection string (hidden): ' DATABASE_URL; echo
export DATABASE_URL SECRET_KEY=placeholder JWT_SECRET_KEY=placeholder FLASK_APP=app.py
.venv/bin/flask db upgrade          # or the project venv
unset DATABASE_URL
```

Expect 12 `Running upgrade` lines ending at `47f7dc9962e3`.

**This is the same chain rehearsed end to end** on a disposable PostgreSQL 17:
`flask db upgrade` → `47f7dc9962e3`, 36 tables.

**No double migration.** The Pre-Deploy Command is `flask db upgrade`, which
runs again at Stage 5 and is a **no-op** once the database is already at head —
Alembic applies nothing. Running it here does not conflict with it running
there.

## Stage 5 — Deploy the intended commit

```bash
git push origin main            # Auto-Deploy is Off — this deploys nothing
```

Then in Render, trigger a manual deploy of `4b95bd1`.

### MEASURED 2026-09-22 — resume restores the existing build; it does NOT rebuild

Tested on a disposable Free-plan service (`render-resume-probe`) armed so that
branch HEAD and the deployed commit genuinely differed:

| Time (EDT) | Event |
|---|---|
| 07:05 | COMMIT-B deployed via Auto-Deploy |
| — | Auto-Deploy Off; COMMIT-C pushed to `main`, **not** deployed |
| 07:32 | Suspended |
| 07:34 | Resumed — **no new deployment event** |
| after | Endpoint served **COMMIT-B**, while `main` was COMMIT-C |

**Resume brought back the previously deployed commit and ignored the newer
branch HEAD.** This contradicts Render's own feature request *"Don't trigger a
new build when an application is resumed"* (marked planned, May 2023) — either
the behaviour changed since, or it differs by plan.

**Scope:** one Free-plan service, one occasion. It does **not** establish what
the paid Starter instance does. Treat it as the likely behaviour, never as a
guarantee.

### What this means here, and why it changes nothing about safety

Expect resume to bring back **`fa92abd`** — the currently deployed commit —
rather than the new one. Against a migrated database its boot guard sees
`47f7dc9962e3` ≠ its own head `q1r2s3t4u5v6`, calls `SystemExit(1)`, and the
service **crash-loops without serving**. That looks alarming and is in fact
the guard doing its job. It happens inside a planned outage in which nothing
was serving anyway.

**Both possible paid behaviours are safe. Neither can serve old code against
the new schema:**

| Paid resume behaviour | What happens | Safe? |
|---|---|---|
| Restores existing build (**observed on Free**) | `fa92abd` returns, boot guard exits, service does not serve. Trigger "Deploy latest commit" to recover. | **yes** |
| Rebuilds from branch HEAD | builds the intended commit; Pre-Deploy `flask db upgrade` is a no-op; serves correctly | **yes** |

The difference is one extra deploy cycle, not correctness.

**Resume does not run the Pre-Deploy Command** — no deployment event is
created — so there is no risk of an unexpected second migration either way.

### Consequence for the procedure

**After Stage 4's migration, go straight to deploying the new commit. Do not
treat Resume as the step that brings the service back.**

**MEASURED 2026-09-22: a manual deploy CANNOT be started while suspended.**
The Manual Deploy control is greyed out in the dashboard for a suspended
service. Precisely: *unavailable through the dashboard* — the REST API may
still accept it, which is untested and academic while the dashboard is the
tool in use.

So the order is fixed, with no remaining unknowns:

```
suspend → migrate → resume → manual deploy
```

**Expect `fa92abd` to come back on resume and crash-loop** until the manual
deploy lands. Its boot guard sees `47f7dc9962e3` against its own head
`q1r2s3t4u5v6` and exits. That is the guard working, inside a planned outage
where nothing was serving anyway — but it will look like a broken service for
a few minutes, so do not react to it.

**Both paid behaviours are still carried, because the probe was Free-plan:**

| If paid resume… | Then | Response |
|---|---|---|
| restores the existing build *(observed twice on Free)* | `fa92abd` returns and crash-loops | trigger "Deploy latest commit" |
| rebuilds from branch HEAD *(Render's 2023 feature request implies this)* | the intended commit builds; Pre-Deploy migration is a no-op | nothing — verify and continue |

Do not let the Free-plan result narrow the recovery procedure to one branch.

Order within the deploy, which is already configured and correct:

```
Pre-Deploy : flask db upgrade                    (no-op; already at head)
Start      : STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app
```

Both flags are inline on the start command only, so the Pre-Deploy migration
runs with neither — the boot guard cannot deadlock it and the sweeper cannot
start mid-migration.

## Proven in production by an accidental redeploy, 2026-09-22

`streakfit-api` was manually redeployed on its existing commit `fa92abd` while
the probe experiment was running — the dashboard was on the wrong service.
Production was verified unchanged afterwards: same commit, 13 migrations at
`q1r2s3t4u5v6`, `/health` 200, roll-up PASS, moderation surface still 404.

It cost nothing and incidentally converted three assumptions into
observations:

- **The Pre-Deploy Command runs and is a genuine no-op at head.** Asserted in
  this runbook; now seen in production.
- **The new Start Command boots.**
  `STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app`
  had never actually run anywhere. It came up clean and the guard passed.
- **`STREAKFIT_RETENTION_SWEEPER=1` is inert on `fa92abd`**, as predicted from
  it appearing zero times in that build.

**Operational lesson, worth more than the finding:** two services, one Manual
Deploy button. Every dashboard instruction in this runbook names its service,
and the reader should confirm the service name in the page header before
clicking anything.

## Stage 6 — Verification, in this order

```bash
curl -s https://streakfit.pro/health
curl -s https://streakfit.pro/api/build-identity | jq '{gitSha, migration}'
curl -s https://streakfit.pro/api/verification/self | jq '[.checks[] | {id, status}]'
```

| # | Check | Expected |
|---|---|---|
| 1 | `/health` | `200 {"status":"ok"}` |
| 2 | `gitSha` | `4b95bd1…` — **not** `fa92abd` |
| 3 | `migration` | `appliedCount: 25`, `atHead: true`, `latest: 47f7dc9962e3` |
| 4 | `moderation.evidence_key` | **PASS**, `key_id 235efc374d3363a7` |
| 5 | `moderation.delivery_configured` | PASS |
| 6 | `moderation.delivery_worker` | PASS within ~1 min (UNKNOWN briefly = worker starting, which is deliberate and different from FAIL) |
| 7 | `ratelimit.shared_storage` | **FAIL — expected.** Phase C, not a regression |
| 8 | `/api/reports` | now reachable (401 unauthenticated, not 404) |

**A different `key_id` at step 4 means the deployed app is using a different
key from the one proven recoverable. Stop and investigate.**

### The only check that proves the feature works

Steps 1–8 are configuration. This is delivery:

- [ ] Register a throwaway account, file a `child_safety` report.
- [ ] **Confirm the email arrives** at the configured address.

A `notification_run` row and a green self-check are not an email in an inbox.
Only a person looking in a mailbox closes this, exactly as at A3.

## Two findings from the final readiness review, 2026-09-22

### The evidence-key check cannot name the key when there are no images

`moderation.evidence_key` compares each stored `photo_evidence.key_id` against
the configured key's fingerprint. Production has **zero sealed images**, so at
Stage 6 it reports `PASS — 0 sealed image(s), all under the current key`
**without ever emitting which key that is.**

It is not a false green: with no key configured at all it correctly reports
UNKNOWN. So PASS does prove *a* usable key is present. What it cannot prove
from outside is that the key is `235efc374d3363a7`, the one whose passphrase
is held.

**What assures that today is provenance, not a check.** The value pasted into
Render came straight out of the sealed key file via
`streakfit-clip-secret.sh evidence-key`, so by construction it is that key.
The residual risk is a paste into the wrong field, which Stage 6 cannot catch.

**Proposed one-line improvement, requiring approval:** emit `current_key_id`
in the check's `observed` text. It is non-secret by construction — a truncated
HMAC, and the code says so — and would let Stage 6 confirm the fingerprint
from outside with nothing exposed. Not implemented; this review is read-only.

### Key Value can be created BEFORE Phase B

The deployed build reacts to `RATELIMIT_STORAGE_URI`, **not** to a Key Value
instance existing. Creating the instance has no effect on the old application;
only setting the variable does, and that stays forbidden until the new build
is live.

So C1 can be done in advance, shrinking the per-worker rate-limiting window
from "Phase B plus a provisioning trip" to **one redeploy**. Free plan, no
cost.

## Stage 7 — Failure procedure, per stage

| Stage | If it fails | Reversible? |
|---|---|---|
| 1 backup | Do not proceed. Nothing has changed. | **Fully** |
| 2 snapshot | Do not proceed. | **Fully** |
| 3 suspend | If `/health` still 200, the window is open — do not migrate. | **Fully** |
| 4 migrate | `flask db downgrade q1r2s3t4u5v6`, then Resume. Old build boots (DB back at its head) and serves as before. | **Fully — no user data exists yet** |
| 5 deploy | Old build cannot serve against the migrated schema — expected, and it is the guard working. Fix forward with "Deploy latest commit". If abandoning: `flask db downgrade q1r2s3t4u5v6` first, THEN Resume, so `fa92abd` boots against its own head. | **Fully** |
| 6 verify fails | See below — this is the boundary. | **Depends** |

### The boundary

**Before any user writes under the new schema**, rollback is clean: downgrade
the database, resume the old build. Verified end to end — `flask db downgrade
q1r2s3t4u5v6` reverses all twelve to 16 tables with `user`, `team`,
`challenge`, `coach_turn`, `coach_note` intact.

**After user data exists, there is no lossless rollback. Neither route.**

- **Downgrade** drops ~20 tables. Every report, evidence record, appeal,
  notice, photo, challenge, **guardian link and consent record** is destroyed,
  along with `user.display_name`, `user.age_band` and the new `coach_note`
  columns.
- **Restore a backup or snapshot** loses *all* activity since it was taken —
  including old-schema activity from before the deploy.

Once Stage 6's real report is filed, that report is one of the things a
rollback discards. **Decide before filing it whether you are committed.**

## Stage 8 — Back online, and normal function

- [ ] `/health` 200; `/api/build-identity` shows the new SHA
- [ ] Log in as a real account; load the Daily Mission
- [ ] Complete a mission; streak increments
- [ ] Ask Rickie something — the route broken in the old/new window
- [ ] Post a team message — same
- [ ] `python scripts/verify_all.py --base-url https://streakfit.pro`
      (179 checks; creates only throwaway `qa_smoke_*` accounts, safe against production)

## Stage 9 — Phase C, immediately after, and free

**`RATELIMIT_STORAGE_URI` must not be set until the new build is live.** The
deployed build reads it and has none of this branch's outage protections — no
degrade hook, no `sensitive_when_degraded`, no in-memory fallback. Pointing
the *old* build at Key Value would 500 every throttled route on any blip. That
is the whole reason Phase C comes after Phase B.

- [x] **C1 — DONE 2026-09-22, before Phase B.** `streakfit-ratelimit`:
      Free ($0), Ohio (matching `streakfit-api`), **`noeviction`**,
      persistence off, **Valkey 8.1.0**, external traffic blocked by inbound
      rules, Internal Authentication **off**.

      Two things this settled that were previously documentation-only:
      **`noeviction` IS selectable on the Free plan**, and the engine is
      Valkey 8 — the same major version every outage measurement in
      [rate-limit-backend-outage.md](rate-limit-backend-outage.md) was taken
      against.

      Creating it early is safe and deliberate: the deployed build reacts to
      `RATELIMIT_STORAGE_URI`, never to the instance existing. It shrinks the
      window in which production runs per-worker limits to a single redeploy.

- [ ] C2 — set `RATELIMIT_STORAGE_URI` to the **internal** URL, redeploy.

      **URL form, verified against Valkey 8 rather than assumed:**

      | Internal Authentication | URL | Result |
      |---|---|---|
      | **off** (current) | `redis://<internal-host>:6379` | ✅ `shared backend counting (redis)` |
      | on | `redis://:<password>@<host>:6379` | ✅ works |
      | on | `redis://default:<password>@<host>:6379` | ✅ works |
      | on, but URL has no credentials | `redis://<host>:6379` | ❌ FAIL — reported as DEGRADED, correctly |

      With auth off the URL carries no secret, so C2 is a plain configuration
      value. If Internal Authentication is ever switched on, the URL becomes a
      credential and must be handled like one — and switching it on *without*
      updating the URL fails closed rather than silently unprotected, which is
      the right direction.
- [ ] C3 — confirm `ratelimit.shared_storage` reads **`shared backend counting (redis)`**.

`redis==5.0.8` is already pinned, so no code change is needed.

## Stage 10 — Manual actions, downtime, cost, approvals

**Only the owner can do:** suspend/resume, trigger the deploy, create the Key
Value instance, and hold the passphrases. I have no Render or Neon
credentials.

**Downtime estimate — Option A: 8–15 minutes.** Assumptions, each of which
could be wrong:

| Step | Estimate | Assumption |
|---|---|---|
| Suspend | <1 min | takes effect promptly |
| Migration | 1–2 min | 32.9 MB, 0 coach_note rows; rehearsal was seconds on an empty DB, so this is the least certain figure |
| Build + boot | 3–8 min | `pip install -r requirements.txt` from cold |
| Verification | 2–5 min | before declaring done |

The build dominates. **Option B's downtime is ~30–90 seconds** because the
build happens while the old service still serves — that is the entire
trade-off.

**Cost: none.** No new paid service. Key Value is the free plan. Suspension
cannot add cost. The cron backstops (~$2/month) are **not** part of Phase B or
C and need separate approval.

**Approvals still required:** Phase B execution; Phase C Key Value creation;
later, the cron backstops and the `coach_note` contract migration.

---

## STOP / GO checklist

**GO requires every line.**

- [ ] `git status` clean; `HEAD` = `main` = `4b95bd1cd855e7a9f79cfc781316a54f5a52ddfe`
- [ ] Branch is a fast-forward of `origin/main` — no merge commit
- [ ] Stage 1 printed `READY TO MIGRATE` **within the last 30 minutes**
- [ ] `coach_note rows: 0`
- [ ] Neon snapshot listed and unexpired
- [ ] Evidence key fingerprint `235efc374d3363a7` recorded; passphrase retrievable
- [ ] Six Render variables set; `RATELIMIT_STORAGE_URI` **not** among them
- [ ] Start Command carries `STREAKFIT_RETENTION_SWEEPER=1` **inline**
- [ ] Auto-Deploy **Off**
- [ ] A quiet hour chosen, and you accept ~8–15 minutes of planned downtime
- [ ] You expect the old build to crash-loop between resume and the new
      deploy, and will not mistake it for a failure — measured probe behaviour
      says resume restores `fa92abd`, whose boot guard then stops it
- [ ] You accept that after Stage 6's real report, rollback discards data

**STOP immediately if:** `coach_note rows > 0`; `/health` still answers 200
after suspending; the migration reports anything other than 12 upgrades to
`47f7dc9962e3`; `evidence_key` reports a fingerprint other than
`235efc374d3363a7`; or the Stage 6 email does not arrive.

## Remaining risks

1. **Resume behaviour measured on Free, assumed for paid.** A Free-plan probe
   showed twice that resume restores the existing build without rebuilding.
   The paid Starter instance is untested and Render's own 2023 feature request
   implies the opposite. Both possibilities are safe; they differ only in
   whether one extra deploy cycle is needed, and the procedure carries both.

2. **Migration duration on real data is an estimate**, not a measurement. The
   rehearsal ran against an empty database. This is the least certain number
   in the runbook.

3. **No lossless rollback once user data exists.** Structural, not fixable.

4. **Rate limits stay per-worker between B and C.** Minimised by provisioning
   Key Value in advance — see the review findings above.

5. **The evidence-key fingerprint cannot be confirmed from outside** while
   there are zero sealed images. Provenance covers it; a check does not.

6. **`onboarding@resend.dev` reaches only the account owner.** A branded
   sender needs DNS on `streakfit.pro`, separately approved.

**Settled by measurement, no longer risks:** deploy-while-suspended (not
possible — control greyed out, so the crash-loop is expected rather than
merely possible); the Pre-Deploy command being a no-op at head, and the new
start command booting (both observed in production on 2026-09-22).
