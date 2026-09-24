# Deploy: account deletion that works (branch `fix/account-deletion-fks`)

**Status: PREPARED, NOT RUN.** Nothing here has been pushed, migrated or
deployed. Every step marked **OWNER** needs Tim's explicit go-ahead at the time
it is taken; approval of one step is not approval of the next.

## 1. What ships

| | |
|---|---|
| Base (production now) | `4979354` — migrations at `47f7dc9962e3` |
| Commits | `28ce314` FK-complete deletion · `e58b189` reporter/appellant deletion + migration · the commit carrying this file (closed-report note, admin label, privacy limits, this procedure) |
| Migration | **one**: `08920334bccd` — `report.reporter_user_id`, `appeal.user_id` → nullable. No data changed by the upgrade |
| Static | `static/admin.html` (labels), `static/sw.js` → `streakfit-v0924a` |
| Config / env | none |

Not included: `364e97a`, `15c5eaa` (release-audit docs on
`integrate-product-completion`). They are docs only; merging them first keeps
the audit on `main`, but pushing them is its own decision.

## 2. Why this deploy is different: rollback needs a downgrade

`STREAKFIT_ENFORCE_DB_HEAD=1` makes the old code refuse to boot on the new
schema (verified: `4979354` exits 1, "database is at Alembic revision
'08920334bccd' but the code expects head '47f7dc9962e3'"). Render's
pre-deploy `flask db upgrade` of `4979354` would also not recognise
`08920334bccd`. So **redeploying `4979354` alone does not roll back** — the
database has to be downgraded first, and the downgrade works only while no
report or appeal has lost its person:

```sql
-- read-only: is a downgrade still possible?  0 = yes
select (select count(*) from report where reporter_user_id is null)
     + (select count(*) from appeal where user_id is null);
```

Once that is non-zero (the first reporter or appellant deletes their account),
the only ways back are forward fixes, or the fresh backup below — which loses
everything written after it.

## 3. Pre-flight (read-only)

1. Worktree clean, HEAD is the reviewed commit; `git merge-base --is-ancestor
   4979354 HEAD`; `git log --oneline 4979354..HEAD` lists exactly the commits
   in section 1; `git diff --stat 4979354..HEAD -- migrations/` shows one file.
2. `git fetch origin` then `origin/main` = `4979354` (fast-forward, 0 merges).
3. Production read-only: `/api/build-identity` → `4979354…`, `atHead: true`,
   `latest 47f7dc9962e3`; `/sw.js` → `v0923d`; `/health` 200;
   `/api/verification/self` → 14 PASS / 1 UNKNOWN baseline.
4. `/admin` → pending 0, undelivered notices 0 (so the verify_all run in
   step 7 is the only new moderation traffic).
5. Render: Auto-Deploy **Off**; Start Command unchanged
   (`flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 … gunicorn app:app`).

## 4. Fresh encrypted backup — **OWNER**

Run by Tim, in his own terminal. Claude does not see the connection string and
never generates or suggests a passphrase.

```bash
cd ~/backups/streakfit
read -rsp 'Paste Neon DIRECT connection string (hidden): ' PGURL; echo
export PGURL
./streakfit-backup.sh          # gpg asks for the passphrase itself
unset PGURL
F=$(ls -t streakfit-neondb-production-*.dump.gpg | head -1)
sha256sum "$F"                 # record in the release audit
```

Before any gpg prompt: have the passphrase ready (the GNOME pinentry grabs the
keyboard). Record file, SHA-256, size, and a **delete-by date chosen
explicitly** — the script prints a hard-coded `2026-10-05`, which is not
derived from the backup. It must be well inside 30 days of the backup, or it
voids the 30-day deletion promise; the owner sets it. Confirm the passphrase
used is the one saved in the password manager.

## 5. Restore rehearsal with the pending migration — **OWNER** (passphrase)

```bash
git -C ~/Desktop/Streakfit/streakfit_production_baseline archive <release-sha> \
  | tar -x -C "$(mktemp -d)"     # the release source; pass its path as SFRH_REPO
SFRH_BACKUP="$F" SFRH_SHA=<sha from step 4> SFRH_BEFORE=47f7dc9962e3 \
SFRH_REPO=<that directory> ~/backups/streakfit/streakfit-rehearse.sh
```

Must show: checksum unchanged; restore 0 errors; **1 pending migration
applied (`47f7dc9962e3 → 08920334bccd`)**; head reached; schema vs models 0
differences; release app starts on 127.0.0.1 only; cleanup 0 containers /
listeners / temp files.

## 6. Push and deploy — **OWNER**, one approval each

```bash
git push origin <release-sha>:refs/heads/main      # fast-forward; never --force
```

Render: **Manual Deploy → deploy commit `<release-sha>`** (not "latest").
Expected in the log:

- Pre-deploy: `Running upgrade 47f7dc9962e3 -> 08920334bccd` then
  `Pre-deploy complete!`
- Start: `Database migration check passed (at head 08920334bccd)`,
  `retention_sweeper_started`
- `Your service is live`

A failed pre-deploy stops the deploy and the old instance keeps serving; the
migration is a single `ALTER … DROP NOT NULL` per column, in one transaction.

## 7. Verify production

| check | expect |
|---|---|
| `/api/build-identity` | `<release-sha>`, `atHead: true`, `latest 08920334bccd`, 26 applied |
| `/sw.js` | `streakfit-v0924a` |
| `/health` | 200 |
| `/api/verification/self` | 14 PASS / 1 UNKNOWN, as before |
| `verify_all.py --base-url https://streakfit.pro` | all pass, **including `auth.delete_account_with_dependent_rows`** (a throwaway `qa_smoke_leaver_*` account that blocks someone and deletes itself) |
| `/admin` | the two reporting-restriction actions read "…the reported person…" |
| downgrade-still-possible query (section 2) | 0 |

The suite's side effects, as last time: 4 `qa_smoke_*` accounts, one Smoke Test
team, and **two smoke reports to dismiss in `/admin`** (identify by reporter →
reported ids, as in audit 13.1).

Phone: open `https://streakfit.pro`, reload twice so `v0924a` loads.

## 8. Rollback — **OWNER**

**While the section-2 query returns 0:**

1. `flask db downgrade 47f7dc9962e3` against production (Render shell, or
   locally with the direct connection string supplied as in step 4). The
   running new code works on the old schema except that deleting a reporter
   or appellant would fail — so do step 2 promptly.
2. Render: Manual Deploy of `4979354`. Its pre-deploy upgrade is a no-op and
   the head check passes at `47f7dc9962e3`.
3. Verify as section 7 against the old expectations (`v0923d`, 25 applied).

**Once it is non-zero:** no downgrade. Fix forward, or restore the step-4
backup into a new Neon branch — which discards every write since the backup,
including the deletions themselves.

## 9. After

Record in the release audit: backup file, SHA, delete-by date; rehearsal
result; push range; deploy id; section-7 results; the two smoke reports
dismissed. Delete the backup by its date (`shred -u`), once no rollback to it
is wanted.
