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

## 3. The database, identified before it is touched

> **CORRECTED 2026-09-21.** This section previously said "a scratch Render
> database" and "not from an earlier automatic one", both of which assumed a
> Render-managed Postgres with scheduled backups. **Render does not host this
> database.** Production reaches **Neon**, and Neon takes no automatic dumps.

### 3.1 Which database — this is not rhetorical

The Neon account contains **two projects named `streakfit`**:

| | Region | Size | Last active (2026-09-21) |
|---|---|---|---|
| A | AWS `us-east-2` (Ohio) | 32.9 MB | ~6 minutes |
| B | AWS `us-east-1` (N. Virginia) | 39.86 MB | ~3 months |

**Do not pick one by region or by recency.** `streakfit-api` runs in Ohio and
the idle project is larger, which is exactly the shape of a mistake that looks
reasonable while it destroys the wrong data. Identify it from the connection
string the service actually dials:

- [ ] **ID1.** Render → `streakfit-api` → Environment → reveal `DATABASE_URL`.
      Copy **only** the text after the **final** `@` and before the next `/`.
      That is the host, and it is not a secret. The password is everything
      between the first `:` after the scheme and that final `@` — a literal
      `@` inside a password is percent-encoded as `%40`, which is why the
      LAST one is always the delimiter. **Never paste a full `DATABASE_URL`
      anywhere, including into a chat.**
- [ ] **ID2.** Strip a `-pooler` suffix if present, leaving
      `ep-<id>.<region>.aws.neon.tech`. Note whether it was there — §3.4
      depends on it.
- [ ] **ID3.** In the Neon console, for each project: pick the branch, then
      **Postgres database → Computes**, and read the compute's endpoint ID
      (`ep-…`). This is visible without revealing any password.
- [ ] **ID4.** The project whose endpoint ID matches ID2 **is production**.
      Record its project name, branch and database name. The region in the
      hostname is corroboration, not the basis of the decision.
- [ ] **ID5.** Treat the other project as **unknown**. Do not open it, connect
      to it, reset it or delete it. Identifying production does not require
      knowing what the other one is — but it should eventually be explained
      rather than forgotten.

🛑 **STOP.** Every step below names a database. Running any of them against
the wrong project is the one mistake with no recovery path.

### 3.2 A branch is not a backup

| | **Neon point-in-time branch** | **`pg_dump` file** |
|---|---|---|
| Lives | inside the same project | wherever you put it |
| Time limit | only within the **history window** | none |
| Survives losing the project/account | **no** | **yes** |
| Speed | instant (copy-on-write) | minutes |
| Role here | rehearsal target | **the recovery artifact** |

The history window is per plan and must be **read, not assumed** — Neon
console → project → **Settings → Postgres → History window**:

| Plan | Window |
|---|---|
| **Free** | **6 hours**, cannot be raised |
| Launch | 1 day, up to 7 |
| Scale | 1 day, up to 30 |

On Free, a branch taken before an evening migration has expired by morning.
**Take the dump regardless of the plan. The dump is the rollback.**

### 3.3 Backup, and proving it is a backup

An untested backup is a belief. The restore is the test.

- [ ] **B1.** Take a **fresh `pg_dump`** of the production project identified
      in §3.1. There is no automatic export to fall back on; if nobody runs
      this, no file exists.
      ```
      pg_dump -Fc --no-owner --no-acl "$PGURL" -f streakfit-$(date +%Y%m%d-%H%M).dump
      ```
- [ ] **B2.** Restore it into a **disposable** target — a Neon branch in the
      same project, or a local Postgres container. **Never production, never
      the Virginia project, never anything whose data matters.**
      ```
      pg_restore --no-owner --no-acl -d "$COPY_URL" streakfit-<stamp>.dump
      ```
- [ ] **B3.** Prove the restore is real, not merely present:
      ```
      flask db current          # expect q1r2s3t4u5v6, the production revision
      ```
      and confirm the row counts for `user`, `team` and `coach_turn` are
      plausible rather than zero.
- [ ] **B3a.** Confirm you backed up the **right** database, without querying
      production: compare the copy's `COUNT(*)` on `"user"`, `MAX(id)` on
      `"user"` and `COUNT(*)` on `daily_completion` against what live
      production reports about itself via `GET /api/admin/stats`
      (`total_users`, the highest `recent_users[].id`, `completions_all_time`).
      A match is independent confirmation; a mismatch means stop.
- [ ] **B4.** Against that restored copy, run the upgrade:
      ```
      flask db upgrade          # expect 12 revisions, ending 47f7dc9962e3
      ```
      This is the rehearsal that matters — the earlier two used synthetic
      data. **If this fails, stop. Nothing below runs.**
- [ ] **B5.** Start the app against the copy and confirm
      `/api/verification/self` returns **15 checks**.
- [ ] **B6.** Destroy the disposable copy. On Free, branches are capped at 10
      per project — delete rehearsal branches rather than collecting them.

Record the dump's filename and timestamp where you will find it while
something is going wrong.

### 3.4 Pooled versus direct, and version compatibility

Neon publishes two hostnames for the same endpoint:

```
direct  ep-<id>.<region>.aws.neon.tech
pooled  ep-<id>-pooler.<region>.aws.neon.tech
```

**Use the DIRECT hostname for `pg_dump`, `pg_restore` and every
`flask db upgrade`.** The pooled host routes through PgBouncer in transaction
mode, which breaks session-scoped behaviour that the dump tools and Alembic
DDL depend on — and it fails confusingly rather than obviously.

This has a direct bearing on §1's Pre-Deploy change: **a Pre-Deploy
`flask db upgrade` inherits the service's `DATABASE_URL`.** If ID2 found
`-pooler`, confirm the upgrade runs against a direct connection before relying
on it, or the migration step is built on the wrong kind of connection.

**Tool versions:** `pg_dump`/`pg_restore` must be the same major version as
the Neon server or newer, or they refuse outright. Check `SHOW
server_version;` against `pg_dump --version` now, not during an incident.

**Permissions:** the role in `DATABASE_URL` is normally `neondb_owner`, which
owns the database and is sufficient for both dumping and DDL. Neon grants no
superuser, and none is needed.

### 3.5 Handling the dump itself

The file contains every user's data, including coach conversations. It is the
most concentrated copy of personal data this project produces.

- Keep the connection string out of shell history — a leading space, or read
  it from a file.
- Store it encrypted and off Neon. Not in the repository, not in `/tmp`, not
  in a syncing cloud folder.
- **Delete it on a schedule you actually keep.** StreakFit promises 30-day
  deletion of conversations and evidence; a dump taken today still contains
  what is deleted tomorrow. An undeleted backup silently outlives the promise.
  **This is unresolved — see §8.**

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

## 4b. What a FAILED migration actually leaves behind

**Measured, not assumed.** The concern is real in general: if Alembic applied
each revision in its own transaction, a failure at revision 7 of 12 would
leave production at an intermediate schema no rollback procedure here
describes.

`migrations/env.py` calls `context.configure(connection=…)` with no
`transaction_per_migration`, so Alembic's default applies: **the entire run is
one transaction.** PostgreSQL has transactional DDL, so that is real rather
than nominal.

Verified experimentally on PostgreSQL 17 with a three-revision chain whose
middle revision raises: **nothing survived** — not the first revision's table,
not even the `alembic_version` row. All-or-nothing.

All 12 pending migrations were scanned for the things that would break this —
`CREATE INDEX CONCURRENTLY`, explicit `COMMIT`, `autocommit`, isolation-level
changes, a second engine or session — and **none is present**. Four of them
change data as well as schema; that rolls back with everything else.

So: **a failed pre-deploy migration leaves production byte-for-byte
unchanged**, and the old version keeps serving. There is nothing to undo, and
the correct response is to read the error, fix it locally, and try again —
*not* to restore a backup.

**The conditions this depends on.** If any later migration introduces
`CONCURRENTLY`, an explicit commit, or a non-transactional operation, or if
`transaction_per_migration` is ever set, this guarantee is void and partial
application becomes possible again. Re-check before any future release.

### ⚠️ One migration deletes production data on SUCCESS

`t4u5v6w7x8y9` (Coach Notes → allow-list) runs:

```sql
DELETE FROM coach_note;
```

and then drops the `goals`, `preferences` and `notes` columns. Every stored
coach note is discarded and re-derived from future conversation under the new
rules. **This is intentional** — old free-text notes cannot be carried into a
closed vocabulary — but it is permanent on success, not on failure.

Before deploying, note how many rows `coach_note` holds in production, so the
loss is a number somebody decided to accept rather than a surprise. The
encrypted backup preserves them; nothing in the application will.

## 5. Rollback, and what it cannot undo

| Situation | Action | Works? |
|---|---|---|
| **Pre-deploy migration failed** | **Nothing.** The whole run rolled back (§4b); the old version never stopped serving | **Yes** — fix and retry |
| Bad code, schema fine | Redeploy `fa92abd` | **Yes.** The new migrations only add; `fa92abd` ignores what it does not know about |
| Migration failed partway | Restore §3.3's dump | Yes, losing everything written since |
| Migration succeeded, data wrong | Restore §3.3's dump | Yes, same loss |
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
2. **§3.1 ID4** — the production Neon project is identified by endpoint ID, not by region or recency.
3. **§3.3 B4** — the rehearsal on restored production data passed.
4. **§4 D4** — merge, knowing it migrates production immediately.
5. **§4 D7** — the post-deploy board shows the expected failures and no
   others.
6. Then, separately, the notification stages in
   `docs/operations/notification-rollout.md` — the sweeper, the provider, the
   end-to-end test, the cron.

---

## 7. What this deploy does not do

It does not send a single alert. `STREAKFIT_NOTIFY_CHANNEL` stays unset
through every step above, so the notification subsystem deploys **switched
off** and says so. Turning it on is a separate, later, reversible decision —
and no one may claim alerts work until a real message has arrived in a real
inbox.

---

## 8. Unresolved, and deliberately not claimed as solved

These are owner decisions. None is fixed by this deploy, and none should be
described as handled because the machinery around it works.

### 8.1 Backup retention versus the 30-day deletion promise

StreakFit tells people their conversations and reported evidence are deleted
after 30 days. **A `pg_dump` taken before a deletion still contains what was
deleted**, and so does any Neon history-window state from before it. The live
database honours the promise; copies of it do not.

Nothing in this repository can reach those copies. What is needed is a
decision — how long dumps are kept, where, and who deletes them — and until
that exists, the honest statement is that **the 30-day promise holds for the
live database only.**

### 8.2 Evidence encryption at launch

`STREAKFIT_EVIDENCE_KEY` is **absent from the live service**, so no photo
evidence is being captured at all. That is fail-closed by design and
`moderation.evidence_key` reports it — but a report filed today preserves no
image. Setting a key is a decision, not a default.

### 8.3 Key rotation has no path

A new `STREAKFIT_EVIDENCE_KEY` makes every image sealed under the old key
**permanently unreadable**. There is no re-encryption path, and losing the key
loses the evidence.

`moderation.evidence_key` now *detects* this by comparing key fingerprints —
it cannot undo it. **Detection is not mitigation**, and this check must not be
read as making rotation safe.

### 8.4 The second Neon project

`streakfit` in `us-east-1`, 39.86 MB, idle for three months, unidentified. It
is excluded from every procedure here and must not be touched. It should be
explained at some point rather than quietly left in the account.
