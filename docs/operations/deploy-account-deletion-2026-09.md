# Deploy: account deletion that works (branch `fix/account-deletion-fks`)

**Status: PREPARED, NOT RUN.** Nothing here has been pushed, migrated or
deployed. Every step marked **OWNER** needs Tim's explicit go-ahead at the time
it is taken; approval of one step is not approval of the next.

## 1. What ships

| | |
|---|---|
| Base (production now) | `4979354` — migrations at `47f7dc9962e3` |
| Commits | `28ce314` FK-complete deletion · `e58b189` reporter/appellant deletion + migration · `88af476` closed-report note, admin label, privacy limits, this procedure · `0b5c05d` first-review fixes · `358af56` section-10 decisions, race locks, CLAUDE.md · `afda493` second-review fixes · the docs-only commit carrying this line. **Code last changed in `afda493`; all checks ran there.** |
| Migration | **one**: `08920334bccd` — `report.reporter_user_id`, `appeal.user_id` → nullable. No data changed by the upgrade |
| Static | `static/admin.html` (labels), `static/sw.js` → `streakfit-v0924a` |
| Config / env | none |

Not included: `364e97a`, `15c5eaa` (release-audit docs on
`integrate-product-completion`). They are docs only; merging them first keeps
the audit on `main`, but pushing them is its own decision.

## 2. Why this deploy is different: rollback needs a downgrade

Two things stop `4979354` from running on the new schema, both verified on
PostgreSQL 17:

- Render's **Pre-Deploy** `flask db upgrade` of `4979354` exits 1: "Can't
  locate revision identified by '08920334bccd'". The deploy stops there and
  the running instance keeps serving.
- If it got past that, `STREAKFIT_ENFORCE_DB_HEAD=1` makes every gunicorn
  worker refuse to start ("database is at Alembic revision '08920334bccd' but
  the code expects head '47f7dc9962e3'"). Note the shape of that failure: the
  gunicorn **master keeps running and listening** while its workers die and
  respawn, so `/health` **times out** rather than failing fast.
 So **redeploying `4979354` alone does not roll back** — the
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

**Things that close the rollback window, besides real users:**
`scripts/cleanup_qa_smoke.py --execute` (it now deletes QA accounts that filed
reports and own no team — verify_all's `qa_smoke_a_*` owns the Smoke Test team
so it stays, but earlier runs' reporters, and any hand-made QA reporter, go),
and any operator deletion of a reporter. **Do not run the QA cleanup until rollback is no longer wanted**, and
re-run the query immediately before any rollback — never rely on an earlier
reading.

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
5. Render: Auto-Deploy **Off**; **Pre-Deploy Command** `flask db upgrade`;
   **Start Command** `STREAKFIT_ENFORCE_DB_HEAD=1 STREAKFIT_RETENTION_SWEEPER=1
   gunicorn app:app` (as `render.yaml` and deployment-sequence.md; §12.3 of the
   release audit saw exactly this). Any difference: stop and reconcile first.
6. Crons: `render.yaml` declares crons on `main` with
   `STREAKFIT_ENFORCE_DB_HEAD=1`. Confirm in the dashboard they are still not
   created. If any exist, they redeploy with this push and are part of the
   rollback too.

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
D=$(mktemp -d)                  # the release source, kept for the next steps
git -C ~/Desktop/Streakfit/streakfit_production_baseline archive <release-sha> | tar -x -C "$D"
SFRH_BACKUP="$F" SFRH_SHA=<sha from step 4> SFRH_BEFORE=47f7dc9962e3 \
SFRH_REPO="$D" ~/backups/streakfit/streakfit-rehearse.sh
```

Must show: checksum unchanged; restore 0 errors; **1 pending migration
applied (`47f7dc9962e3 → 08920334bccd`)**; head reached; schema vs models 0
differences; release app starts on 127.0.0.1 only; cleanup 0 containers /
listeners / temp files.

**Rehearse the rollback too, on the same restored copy**, before it is torn
down (the rehearsal script only runs the upgrade; this needs a manual step or
a small extension to the script, from the release source `$D`, with
`STREAKFIT_ENFORCE_DB_HEAD` and `STREAKFIT_RETENTION_SWEEPER` unset):
`flask db downgrade 47f7dc9962e3` → exit 0 and both columns `NOT NULL` again;
the section-2 query → 0; `flask db upgrade` → back at `08920334bccd`. This is
the only time the rollback runs against real data before it might be needed.

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

The suite's side effects, as last time: 4 `qa_smoke_*` accounts plus one
`qa_smoke_leaver_*` that deletes itself, one Smoke Test team, and **two smoke
reports to dismiss in `/admin`** (identify by reporter → reported ids, as in
audit 13.1). Each report creates a `report_filed` notice, so **two real emails
arrive** through the live channel; if they are not dismissed within 54 hours,
`deadline_approaching` and then `overdue` emails follow. While they are
pending, `qa_smoke_b_*` cannot be deleted (a pending report names it). None
of this touches the section-2 rollback query: the leaver is not a reporter and
`qa_smoke_a_*` is never deleted.

Phone: open `https://streakfit.pro`, reload twice so `v0924a` loads.

## 8. Rollback — **OWNER**

**While the section-2 query returns 0:**

0. Re-run the section-2 query now. Non-zero: stop, this path is closed.
1. `flask db downgrade 47f7dc9962e3` against production, **run from the
   release source** (`$D`, or the Render shell of the release instance) —
   `4979354` cannot do it: it does not know `08920334bccd` ("Can't locate
   revision"). Environment: `STREAKFIT_ENFORCE_DB_HEAD` and
   `STREAKFIT_RETENTION_SWEEPER` **unset**; the direct connection string
   supplied as in step 4. The running release code then works on the old
   schema, except that deleting a reporter or appellant returns 500 and rolls
   back (verified) — so do step 2 promptly. **If Render restarts the release
   instance before step 2**, its head check refuses to start it and the site
   is down until step 2 completes.
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

## 10. Owner decisions from the independent review — DECIDED 2026-09-24

Each is implemented and tested; the reasoning is in privacy-retention.md.

1. **Pending or held report names them → deletion refused** (409,
   `safety_record`, no detail). Also a reporter of a held report.
2. **Suspension:** deleted with the account; the `ModerationAction` stays
   (documented, unchanged behaviour).
3. **Challenge addressed to them:** expired at deletion, not reopened to the
   team.
4. **Deletion log lines:** no user id.
5. **Races:** fixed, not accepted. Deletion takes `FOR UPDATE` on the person;
   report filing, legal hold, report actions and appeal decisions take
   `FOR KEY SHARE` first and re-read. Six orderings tested on PostgreSQL with
   the second request proven to wait; with the locks removed, 16 of 18 checks
   fail, including a foreign-key 500.

Second independent review (2026-09-24), fixed in the final commit:

- **Owner oracle (blocker):** a team owner always gets the team answer, so
  polling `DELETE /api/me` no longer reveals a report about them.
- **Legal hold vs a deleting reporter:** report actions now lock both the
  reporter and the reported person (id order); a hold placed first now blocks
  the reporter's deletion (409).
- **Two simultaneous deletions deadlocking:** retried; 40/40 natural
  collisions end 200/200 (was 37/40 with a 500).
- **Sweeper vs legal hold** (pre-existing): the sweep locks each report
  (`SKIP LOCKED`) and re-reads the hold.
- **Challenge evidence kept the deleted person's id:** cleared at deletion,
  except under legal hold.
- **Team challenge addressed to someone deleting:** now 400
  `not_a_team_member`, not 500; a reporter filing while deleting in another
  tab gets 404, not 500. **Blocks were already safe** (`create_block` catches
  the IntegrityError; 204).

Still open, for the owner:

- **A reporter can unlink themselves from a PENDING child_safety report.**
  The report stays pending with its note; investigators lose the person to
  follow up with. Decision 1 only protects people the report names. Options:
  block a reporter's deletion while any report they filed is pending (or only
  child_safety), or accept.
- **An expired challenge addressed to a deleted person reads "challenged the
  team"** in history, because NULL target means everyone. Nobody can complete
  it; the card text is wrong. Fix is display-side (e.g. "challenged a former
  member" when expired with no target and a creator) — not in this release.
- **Deadlock retry costs ~1 s** per colliding pair; a stable lock order across
  shared rows would avoid it. Latent while production runs one worker.
