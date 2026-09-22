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

### ⚠️ RESEARCHED 2026-09-22 — resume triggers a build, and the commit is undocumented

**Established:** resuming a suspended Render service **automatically triggers
a new build and deploy.** Render's own feature-request tracker carries
*"Don't trigger a new build when an application is resumed"*, whose text is
this exact scenario — *"Sometimes there's a need to suspend and then resume a
service (for example, when performing maintenance actions like migrations).
This is inconvenient when Render triggers a new build when the service is
resumed."* Marked **"planned"** by Render in May 2023 and still planned, so
the behaviour stands.

**NOT established, after searching the scaling, deploys, FAQ and API docs:**

- **which commit** that automatic build uses — branch HEAD, or the
  last-deployed commit;
- whether a **manual deploy can be triggered on a suspended service** at all.

Render's deploy documentation describes "Deploy latest commit" (branch HEAD)
and "Deploy a specific commit", and says nothing about suspended services.

**Why this does not make the procedure unsafe — only uncertain in duration.**
Both outcomes are fail-closed:

| Resume builds… | Result |
|---|---|
| the new commit | correct: new code, migrated schema, serving |
| `fa92abd` | boot guard sees DB at `47f7dc9962e3` ≠ its head `q1r2s3t4u5v6`, calls `SystemExit(1)`, **service does not serve**. Trigger "Deploy latest commit" to recover |

In neither branch does old code serve against the new schema. The cost of the
bad branch is one failed deploy cycle and a crash-looping service that looks
alarming, not a data or correctness risk.

**Push the intended commit to `main` BEFORE suspending**, so that if resume
does build branch HEAD, it builds the right thing.

Order within the deploy, which is already configured and correct:

```
Pre-Deploy : flask db upgrade                    (no-op; already at head)
Start      : STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app
```

Both flags are inline on the start command only, so the Pre-Deploy migration
runs with neither — the boot guard cannot deadlock it and the sweeper cannot
start mid-migration.

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

## Stage 7 — Failure procedure, per stage

| Stage | If it fails | Reversible? |
|---|---|---|
| 1 backup | Do not proceed. Nothing has changed. | **Fully** |
| 2 snapshot | Do not proceed. | **Fully** |
| 3 suspend | If `/health` still 200, the window is open — do not migrate. | **Fully** |
| 4 migrate | `flask db downgrade q1r2s3t4u5v6`, then Resume. Old build boots (DB back at its head) and serves as before. | **Fully — no user data exists yet** |
| 5 deploy | Old build cannot serve against the migrated schema. Either fix forward, or downgrade as above then Resume. | **Fully** |
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

- [ ] C1 — New → Key Value, **free plan**, same region, **`noeviction`**.
      Confirm at creation that `noeviction` is offered and the workspace's one
      free instance is unused. If either is not true, **stop** — do not
      upgrade to a paid plan to work around it.
- [ ] C2 — set `RATELIMIT_STORAGE_URI` to the **internal** URL, redeploy.
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
- [ ] You accept that after Stage 6's real report, rollback discards data

**STOP immediately if:** `coach_note rows > 0`; `/health` still answers 200
after suspending; the migration reports anything other than 12 upgrades to
`47f7dc9962e3`; `evidence_key` reports a fingerprint other than
`235efc374d3363a7`; or the Stage 6 email does not arrive.

## Remaining risks

1. **Suspend semantics undocumented** — mitigated by the boot guard, which
   makes an old instance unable to return against the migrated schema.
2. **Resume-vs-deploy ordering unverified** — may briefly show the old build
   crash-looping; that is the guard working. Confirm live at Stage 5.
3. **Migration duration on real data is an estimate**, not a measurement. The
   rehearsal ran against an empty database.
4. **No lossless rollback after user data exists.** Structural, not fixable.
5. **Rate limits stay per-worker between B and C** — keep the gap short.
6. **`onboarding@resend.dev` reaches only the account owner.** A branded
   sender needs DNS on `streakfit.pro`, separately approved.
