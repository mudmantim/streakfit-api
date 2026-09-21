# Moderation alert rollout

How StreakFit goes from "notices are generated and nobody is told" to "a real
email reaches Tim", in stages that can each be stopped, and without ever
claiming the alerts work before one has actually arrived.

Nothing in this document has been done. It is the plan, written down so the
order is reviewable before anything is irreversible.

---

## 1. What is true right now

Verified in the Render dashboard on 2026-09-21, and against the running
build's own `/api/build-identity`:

| | |
|---|---|
| Service | `streakfit-api`, **Starter** (paid — does **not** spin down), region **Ohio** |
| Repo / branch | `mudmantim/streakfit-api`, `main`, **auto-deploy On Commit** |
| Live commit | `fa92abd` |
| Start Command | `flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app` |
| Pre-Deploy Command | *(empty)* |
| Env vars present | `ADMIN_SECRET`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `JWT_SECRET_KEY`, `SECRET_KEY` |
| `STREAKFIT_RETENTION_SWEEPER` | **absent** |
| `WEB_CONCURRENCY` | **absent** → gunicorn's default of **one worker**, so one sweeper thread |
| Cron jobs | **none** |

Two consequences worth stating plainly:

**Nothing sweeps and nothing delivers in production today**, and that is not a
misconfiguration — the live build contains no sweeper, no CLI commands and no
moderation tables at all. The `STREAKFIT_RETENTION_SWEEPER` flag has nothing
to switch on until this branch deploys.

**`STREAKFIT_EVIDENCE_KEY` is absent**, so no photo evidence is being captured.
That is fail-closed by design — a reviewer sees `no_evidence_key` rather than
an image — but it means a real report filed today preserves no photo.

---

## 2. The two runners, and why both

| | In-process worker | Render cron |
|---|---|---|
| Runs when | gunicorn is up | always, independently |
| Misses | deploys, restarts, crash loops | nothing |
| Cost | £0 | $1/month minimum each |
| Records `source` | `thread` | `cron` |

Either satisfies `moderation.delivery_worker`; a **manual** run never does.

Running both is safe, and it is asserted rather than argued — see
`tests/test_unattended_operation.py`. Delivery takes a durable five-minute
lease committed *before* the send, so two runners racing the same notice
produce exactly one send and the loser skips. Retention deletes are
idempotent; notice generation is uniqueness-constrained.

The concrete cron specifications — schedules, commands, and the environment
variables each job needs — are in `render.yaml`. That file is **inert**:
Render does not read it for this service, so the blocks there are something to
type into *New Cron Job*, not something to switch on.

**The crons cannot be created before the deploy.** `flask moderation-notify`,
`coach-prune` and `moderation-prune` do not exist in `fa92abd` — it registers
no CLI commands at all — so a cron created today would fail every run.

---

## 3. The migration problem, and what to do about it

The Start Command is `flask db upgrade && … gunicorn app:app`, and auto-deploy
is **On Commit**. Together those mean:

> **Merging to `main` deploys and migrates production, immediately, with no
> gate in between.** There is no way to approve the 12 migrations separately
> while that command stands.

There is no safe way to pretend otherwise. Pick one before merging:

**Option A — accept it (simplest).** Do every preparation step first, merge
deliberately, and treat the merge itself as the deploy approval. The
migrations have been rehearsed end-to-end on PostgreSQL 16 (`13 → head`, then
`empty → head`, zero schema drift against the models), so the risk is low and
the rollback is the database backup.

**Option B — move migrations to Pre-Deploy (recommended, needs a Render
change).** The Pre-Deploy Command is currently empty. Moving the upgrade there
is strictly better:

```
Pre-Deploy Command:  flask db upgrade
Start Command:       STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1 gunicorn app:app
```

In the start command the upgrade re-runs on **every process start** — restart,
scale, crash recovery — where at best it is a no-op. As a pre-deploy step it
runs **once per deploy**, and a failure aborts the deploy *before* the new
version serves traffic. That is what "migrations run as an explicit deploy
step, never inside the app on boot" in CLAUDE.md is actually asking for.

**Option C — turn auto-deploy off** for the duration, deploy by hand, turn it
back on. Most control, most steps, easiest to leave in the wrong state.

---

## 4. Rollout stages

Each stage is reversible, and no stage claims delivery works until stage 6
proves it.

### Stage 0 — before anything
- [ ] **Identify which Neon project is production** by matching the hostname
      in `DATABASE_URL` to an endpoint ID — the account holds two projects
      named `streakfit`, and region is not the tiebreak. See
      [deploy-runbook.md](deploy-runbook.md) §3.1.
- [ ] `pg_dump` the production database **and restore it somewhere
      disposable** to prove the backup is real. Neon takes no automatic dump
      export, and a point-in-time branch is not a backup — §3.2.
- [ ] Decide Option A / B / C above.

### Stage 1 — deploy the code, with delivery switched off
- [ ] Merge to `main`. Leave `STREAKFIT_NOTIFY_CHANNEL` **unset**.
- [ ] Confirm `/api/build-identity` reports the new `gitSha` and
      `migration.atHead: true` with 25 applied.

Expected, and correct: `moderation.delivery_configured` **FAIL**,
`delivery_worker` **FAIL**, retention **UNKNOWN**. The board is red because
nothing has been set up yet, not because anything broke.

### Stage 2 — turn the worker on
- [ ] Add `STREAKFIT_RETENTION_SWEEPER=1` **inline on the start command**,
      never as a global env var (as a global it fires during `flask db
      upgrade` and deadlocks the deploy).
- [ ] Wait ~1 minute, then re-read `/api/verification/self`.

`delivery_worker` should turn **PASS** within a minute — the worker settles
for 20 seconds, then works, then sleeps. Inside that window the check reports
**UNKNOWN** ("a worker started Ns ago … first pass has not completed yet"),
which is deliberately different from the FAIL a stopped worker gets.

Retention checks turn PASS on their own once a sweep is recorded.

### Stage 3 — Resend account, no DNS yet
- [ ] Create the Resend account; generate an API key. **Tim generates it; it
      is never shared into a transcript.**
- [ ] Set the three variables in Render: `RESEND_API_KEY`,
      `STREAKFIT_NOTIFY_FROM`, `STREAKFIT_NOTIFY_TO`. Leave
      `STREAKFIT_NOTIFY_CHANNEL` unset for now — half-configured is not
      configured, and the app will keep reporting delivery off until all of
      it is in place.
- [ ] Run the preflight, which reads the variables and **prints none of their
      values**:

      python scripts/notification_preflight.py          # offline; shape only
      python scripts/notification_preflight.py --live   # + asks Resend if the
                                                        #   key is accepted

      `--live` makes one read-only call to Resend's `/domains` endpoint. It
      sends no email and costs nothing. Exit 0 ready, 1 a problem, 2 nothing
      configured.

      This catches the half-configured state, a wrong secret pasted into
      `RESEND_API_KEY`, and a recipient list where one address is required —
      all before an undelivered child-safety alert is the thing that tells
      you. It cannot tell you mail actually arrives; only stage 4 does that.

### Stage 4 — the real end-to-end test, still without DNS
Resend's `onboarding@resend.dev` sends only to the **account owner's own
address** — and the account owner is the alert recipient. So the first real
delivery test needs no DNS at all:

- [ ] Set `STREAKFIT_NOTIFY_CHANNEL=resend`, `RESEND_API_KEY=…`,
      `STREAKFIT_NOTIFY_FROM=onboarding@resend.dev`,
      `STREAKFIT_NOTIFY_TO=<Tim's address>`, `STREAKFIT_PUBLIC_URL=https://streakfit.pro`.
- [ ] Re-run `python scripts/notification_preflight.py --live`. With
      `onboarding@resend.dev` it passes and reminds you of that sender's one
      restriction: it delivers **only to the account owner's own address**.
- [ ] File a throwaway `child_safety` report against a test account.
- [ ] **Confirm the email arrives.**

Only now may anyone say alerts work. Before this, every claim is about
machinery, not about delivery.

### Stage 4b — photo evidence key (independent of email)

`STREAKFIT_EVIDENCE_KEY` is unset today, so reported images are not captured
at all and a reviewer is shown `no_evidence_key`. That is fail-closed by
design and is safe to leave as it is. Turning it on has one rule.

**The key is not recoverable from anywhere.** It is not in the database, not
in the repository, and not derivable from the ciphertext. Lose it and every
sealed image is permanently unreadable — including the evidence behind an open
child-safety report. So it is proven recoverable *before* it is activated,
never after.

`~/backups/streakfit/streakfit-evidence-key.sh` does this. It never prints the
key; it displays only the non-secret fingerprint the app records in
`photo_evidence.key_id`.

- [ ] `streakfit-evidence-key.sh new` — generates a Fernet key, seals it with
      a gpg passphrase (AES-256), records the fingerprint. The plaintext key
      never touches disk and is never an argument, so it reaches neither
      `ps` nor shell history. Refuses to overwrite an existing sealed key.
- [ ] **Store the passphrase somewhere independent of this machine** — a
      password manager. The sealed file and its passphrase must not share a
      single point of failure.
- [ ] `streakfit-evidence-key.sh verify` — **in a new shell**, typing the
      passphrase from memory rather than from scrollback. This is the gate.
      It decrypts the file, checks gpg's integrity check, confirms the result
      is a usable Fernet key that round-trips a test message, and matches the
      recorded fingerprint. All four, or it refuses.
- [ ] Only once that passes: `streakfit-evidence-key.sh reveal`, and paste the
      value into Render as `STREAKFIT_EVIDENCE_KEY`. Clear the scrollback.
- [ ] Confirm `/api/verification/self` reports `moderation.evidence_key` PASS
      with that fingerprint.

**Rotation, if it ever happens:** keep the old sealed file. The app records
which key sealed each row, and the self-check reports rows sealed under a key
that is no longer current. Retiring the old key makes everything sealed under
it unreadable forever.

### Stage 5 — the independent cron
- [ ] Create `streakfit-moderation-notify` from the spec in `render.yaml`
      (hourly, `flask moderation-notify --scheduled`).
- [ ] Watch one run; confirm a `notification_run` row with `source='cron'`.
- [ ] Optionally create `streakfit-retention-sweep` (daily).

### Stage 6 — branded sender
- [ ] At **Namecheap** (`dns1/dns2.registrar-servers.com` — the domain has no
      MX and no TXT today, so nothing conflicts), add Resend's records for
      `alerts.streakfit.pro`: 1 MX, 1 SPF TXT, 1 DKIM TXT.
- [ ] Switch `STREAKFIT_NOTIFY_FROM` to the branded sender.
- [ ] **Repeat stage 4's end-to-end test.** A sender change is a delivery
      change and re-earns its proof.

---

## 5. Rollback

| Problem | Action |
|---|---|
| Alerts misbehaving | Unset `STREAKFIT_NOTIFY_CHANNEL`. Delivery stops and claims nothing; notices keep accruing. |
| Worker misbehaving | Remove `STREAKFIT_RETENTION_SWEEPER=1` from the start command. |
| Cron misbehaving | Suspend the cron job. |
| The deploy itself | Redeploy `fa92abd`. **Migrations are forward-only in practice — restore the backup, do not downgrade.** |

---

## 6. What this still does not buy

*(Resolved since this list was written: `report_filed` and
`deadline_approaching` both exist now — all six alert kinds fire, and
`_NOTICE_PRIORITY` orders them in tiers so an urgent notice is never queued
behind a routine one.)*

- **Resend's idempotency window is 24 hours; urgent retries are unbounded.** A
  notice failing for longer than a day and then retrying reuses a key Resend
  has forgotten, so the crash-window protection expires exactly when a long
  outage makes it most relevant.
- **The free tier caps at 100 emails/day** (3,000/month). On a report-flood
  day that cap is itself a delivery failure, and the retry will keep pushing
  against a ceiling it cannot clear until the next day.
- **Nothing alerts on the alerter.** If Resend is down, the thing that would
  tell you is the thing that is down. That wants a second, independent
  channel or an external dead-man's-switch, and neither exists.
- **`STREAKFIT_EVIDENCE_KEY` is unset**, so photo evidence is not captured.
  The custody tooling now exists (stage 4b) but the key has not been generated
  or activated, and until it is, a reviewer sees `no_evidence_key`.
- **Shared rate-limit storage is not provisioned**, so limits are still
  per-worker and reset on deploy. The outage behaviour has been measured and
  fixed ahead of provisioning — see
  [rate-limit-backend-outage.md](rate-limit-backend-outage.md) — but the plan
  choice is still open, and Render's free Key Value tier loses all counters
  whenever its instance restarts.
