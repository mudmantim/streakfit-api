# Deploy runbook: the 12-migration release

For the one deploy that takes production from `fa92abd` (13 migrations, no
moderation subsystem) to the integration branch (25 migrations). Written
because this deploy has a property the previous ones did not: **there is no
gate between merging and migrating.**

Nothing here has been done. Every step needs Tim.

---

## 1. The constraint, stated first

Verified in the Render dashboard on 2026-09-21:

```
Auto-Deploy:          On Commit
Start Command:        flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app
Pre-Deploy Command:   (empty)
```

Therefore:

> **A commit reaching `main` deploys the code and runs `flask db upgrade`
> against the production database, automatically, with nothing in between.**

The migrations cannot be approved separately while that command stands. Any
plan that assumes "merge now, migrate later" is wrong about this service.

### Three ways to get a gate

| | What | Cost |
|---|---|---|
| **A** | Accept it. Merge *is* the deploy approval. | Nothing to change; least control |
| **B** *(recommended)* | Move `flask db upgrade` into the empty **Pre-Deploy Command** | One dashboard change |
| **C** | Turn Auto-Deploy off, deploy by hand, turn it back on | Most control; easiest to leave misconfigured |

**Why B is better regardless of this release.** In the Start Command the
upgrade re-runs on *every process start* — restart, scale, crash recovery —
where at best it is a no-op and at worst it is a migration attempt nobody
asked for. As a Pre-Deploy step it runs **once per deploy**, and a failure
**aborts the deploy before the new version serves traffic**. That is what
CLAUDE.md means by "migrations run as an explicit deploy step, never inside
the app on boot".

```
Pre-Deploy Command:  flask db upgrade
Start Command:       STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app
```

`STREAKFIT_ENFORCE_DB_HEAD` must stay **inline on gunicorn** and never become
a global environment variable — as a global it fires during the upgrade and
deadlocks the deploy. The same is true of `STREAKFIT_RETENTION_SWEEPER`.

---

## 2. What is actually being migrated

12 revisions, `q1r2s3t4u5v6` → `47f7dc9962e3`. A single linear chain: one
head, no branch points, no merge revisions.

**Rehearsed, not assumed.** On disposable PostgreSQL 16:

- production's revision built from scratch (13), then upgraded to head (12
  more) — clean;
- an empty database built straight to head (25) — clean;
- **zero schema differences** against the models in both cases (Alembic
  `compare_metadata`).

These migrations **add** tables and columns. None drops a table or a column
that `fa92abd` reads. That is what makes the code rollback in §5 survivable.

---

## 3. Backup, and proving it is a backup

An untested backup is a belief. The restore is the test.

- [ ] **B1.** Take a fresh backup of the production database immediately
      before the deploy, not from an earlier automatic one.
- [ ] **B2.** Restore it into a **disposable** database — a local container
      or a scratch Render database, never production, never the staging of
      anything that matters.
- [ ] **B3.** Prove the restore is real, not merely present:
      ```
      flask db current          # expect q1r2s3t4u5v6, the production revision
      ```
      and confirm the row counts for `user`, `team` and `coach_turn` are
      plausible rather than zero.
- [ ] **B4.** Against that restored copy, run the upgrade:
      ```
      flask db upgrade          # expect 12 revisions, ending 47f7dc9962e3
      ```
      This is the rehearsal that matters — the other two used synthetic data.
      **If this fails, stop. Nothing below runs.**
- [ ] **B5.** Destroy the disposable copy.

Record the backup's identifier and timestamp where you will find it while
something is going wrong.

---

## 4. The deploy

- [ ] **D1.** Confirm the branch is green: full suite, migration integrity,
      build check, local verification.
- [ ] **D2.** Confirm `/api/build-identity` still reports `fa92abd`, so you
      know what you are leaving.
- [ ] **D3.** Choose A / B / C from §1. If **B**, make the dashboard change
      **before** merging and note that it takes effect on the next deploy.
- [ ] **D4.** **APPROVAL CHECKPOINT — this is the irreversible one.** Merge.
      With Auto-Deploy On Commit, this migrates production.
- [ ] **D5.** Watch the deploy log for all 12 revisions. A failure here with
      option B aborts before serving; with option A, gunicorn never starts and
      the previous version keeps serving — which is a *working* app on an
      already-migrated database.
- [ ] **D6.** `/api/build-identity` → new `gitSha`, `migration.appliedCount:
      25`, `atHead: true`.
- [ ] **D7.** `/api/verification/self` → expect **FAIL**, and expect it to be
      these and only these:
      - `moderation.delivery_configured` FAIL — no provider yet
      - `moderation.delivery_worker` FAIL — sweeper not enabled yet
      - `moderation.notices_delivered` UNKNOWN
      - `retention.*` UNKNOWN — no unattended sweep recorded yet
      - `moderation.evidence_key` UNKNOWN — no evidence key set

      **Anything else red is not part of the plan. Stop and read it.**

---

## 5. Rollback, and what it cannot undo

| Situation | Action | Works? |
|---|---|---|
| Bad code, schema fine | Redeploy `fa92abd` | **Yes.** The new migrations only add; `fa92abd` ignores what it does not know about |
| Migration failed partway | Restore §3's backup | Yes, losing everything written since |
| Migration succeeded, data wrong | Restore §3's backup | Yes, same loss |
| Want the schema back | — | **No.** Treat the migrations as forward-only |

**The migrations are not reversible in practice.** They have `downgrade()`
functions and those have never been rehearsed against real data. Do not
discover whether they work during an incident. **The backup is the rollback.**

The asymmetry worth internalising: rolling back the **code** is cheap and
safe, because the added tables are simply unread by the old build. Rolling
back the **schema** is neither.

---

## 6. Approval checkpoints

Each is a decision only Tim makes. None is implied by the previous one.

1. **§1** — which gate (A / B / C), and whether to change the Start Command.
2. **§3 B4** — the rehearsal on restored production data passed.
3. **§4 D4** — merge, knowing it migrates production immediately.
4. **§4 D7** — the post-deploy board shows the expected failures and no
   others.
5. Then, separately, the notification stages in
   `docs/operations/notification-rollout.md` — the sweeper, the provider, the
   end-to-end test, the cron.

---

## 7. What this deploy does not do

It does not send a single alert. `STREAKFIT_NOTIFY_CHANNEL` stays unset
through every step above, so the notification subsystem deploys **switched
off** and says so. Turning it on is a separate, later, reversible decision —
and no one may claim alerts work until a real message has arrived in a real
inbox.
