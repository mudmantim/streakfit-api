# Deploy: account deletion that works (branch `fix/account-deletion-fks`)

**Status: PREPARED, NOT RUN.** Nothing here has been pushed, migrated or
deployed. Every step marked **OWNER** needs Tim's explicit go-ahead at the time
it is taken; approval of one step is not approval of the next.

## 1. What ships

| | |
|---|---|
| Base (production now) | `4979354` — migrations at `47f7dc9962e3` |
| Commits | 14, in order: `28ce314` `e58b189` `88af476` `0b5c05d` `358af56` `afda493` `78047de` `d1c59d3` `753780f` `bb96d8d` `0b90d98` `dfd56e2` `0b5b480`, then the commit at the tip that carries this list (its hash is the release commit, given in the release summary) |
| Migration | **one**: `08920334bccd` — `report.reporter_user_id`, `appeal.user_id` → nullable; adds nullable `team_challenge.target_left_at` and `report.deletion_requested_at`. No existing data changed by the upgrade; the downgrade drops both columns |
| Static | `static/admin.html` (labels; "deletion waiting" indicator), `static/app.js` (challenge card), `static/sw.js` → `streakfit-v0924c` |
| Rollback build | `ef3178e` on local branch `rollback/account-deletion-compat` — see section 2. Not part of the release; prepared and tested with it |
| Config / env | none |

Not included: `364e97a`, `15c5eaa` (release-audit docs on
`integrate-product-completion`). They are docs only; merging them first keeps
the audit on `main`, but pushing them is its own decision.

## 2. Rollback is a code deploy, never a database downgrade

`4979354` cannot run on the new schema: Render's Pre-Deploy `flask db
upgrade` of it exits 1 ("Can't locate revision identified by
'08920334bccd'"), and even past that, `STREAKFIT_ENFORCE_DB_HEAD=1` makes
every gunicorn worker refuse to start (the master stays up and `/health`
times out). Downgrading the database first was the previous plan; on
PostgreSQL it broke reporter/appellant deletion, team chat for teams with a
challenge, creating, completing **and reporting** a challenge until the old
code was live, and it became impossible once any reporter or appellant had
deleted their account.

So the rollback target is a **rollback build**, `ef3178e` on branch
`rollback/account-deletion-compat`: `4979354` plus the release's migration
file (byte-identical), the four model declarations that migration implies,
the guard that refuses to decide an appeal whose appellant has gone, and a
distinct service-worker cache (`streakfit-v0924r`). On the release schema its
Pre-Deploy upgrade is a no-op and its head check passes. It never writes the
database differently from `4979354` and does not need the database to change.
It works **after** reporters and appellants have been deleted. Rolling back
means account deletion goes back to `4979354`'s behaviour (it 500s on
PostgreSQL for most users) — which is what rolling back means.

Consequences:

- There is no rollback window and no "rollback closes" query. The QA cleanup
  (`scripts/cleanup_qa_smoke.py --execute`) no longer affects rollback, but
  still waits until the release is verified.
- `flask db downgrade 47f7dc9962e3` remains possible only while no report or
  appeal has lost its person, and is **not** part of this procedure. It is a
  last resort with the failures above; if ever used, use Render maintenance
  mode for its duration.
- The rollback build must stay in step with the release's migration file. If
  the migration changes again before deploy, rebuild and retest it.

## 3. Pre-flight (read-only)

1. Worktree clean, HEAD is the reviewed release commit; `git merge-base
   --is-ancestor 4979354 HEAD`; `git log --oneline 4979354..HEAD` shows **14**
   commits: the 13 listed in section 1, in that order, plus the tip;
   `git diff --stat 4979354..HEAD -- migrations/` shows one file.
   Rollback build: `git -C <rollback worktree> log --oneline 4979354..HEAD` is
   exactly `ef3178e`; `cmp` of its `migrations/versions/08920334bccd_*.py`
   against the release's shows no difference.
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

**Rehearse the rollback too, on the same restored copy, after the upgrade**
(a manual step or a small extension to the rehearsal script): from a
`git archive ef3178e` source, `flask db upgrade` → exit 0 and still at
`08920334bccd`; start it on 127.0.0.1 with `STREAKFIT_ENFORCE_DB_HEAD=1` →
the head check passes; `/health` 200; `/api/admin/reports?status=all` 200.
Then the release source again: `flask db upgrade` → no-op. No downgrade is
rehearsed because none is planned.

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
| `/sw.js` | `streakfit-v0924c` |
| `/health` | 200 |
| `/api/verification/self` | 14 PASS / 1 UNKNOWN, as before |
| `verify_all.py --base-url https://streakfit.pro` | all pass, **including `auth.delete_account_with_dependent_rows`** (a throwaway `qa_smoke_leaver_*` account that blocks someone and deletes itself) |
| `/admin` | the two reporting-restriction actions read "…the reported person…"; the queue loads (the "holding up an account deletion" count appears only once a refusal has happened) |

The suite's side effects, as last time: 4 `qa_smoke_*` accounts plus one
`qa_smoke_leaver_*` that deletes itself, one Smoke Test team, and **two smoke
reports to dismiss in `/admin`** (identify by reporter → reported ids, as in
audit 13.1). Each report creates a `report_filed` notice, so **two real emails
arrive** through the live channel; if they are not dismissed within 54 hours,
`deadline_approaching` and then `overdue` emails follow. While they are
pending, `qa_smoke_b_*` cannot be deleted (a pending report names it), nor
can `qa_smoke_a_*` (it filed them, and owns the team).

Phone: open `https://streakfit.pro`, reload twice so `v0924c` loads.

## 8. Rollback — **OWNER**

Deploy the rollback build. No database change; works whether or not anyone
has deleted an account since the release.

1. Make `main` carry the rollback build's exact tree **without a force-push**:
   ```bash
   git fetch origin && git switch -c rollback-deploy origin/main
   git read-tree -u --reset ef3178e       # working tree := rollback build
   git commit -m "rollback: deploy ef3178e's tree (account-deletion release)"
   git diff ef3178e HEAD --stat           # MUST print nothing
   git push origin HEAD:refs/heads/main   # fast-forward
   ```
2. Render: **Manual Deploy → that commit.** Expect the Pre-Deploy upgrade to
   print no "Running upgrade" and the start log to say `Database migration
   check passed (at head 08920334bccd)`.
3. Verify: `/api/build-identity` → the new commit, `atHead: true`, latest
   `08920334bccd`, 26 applied; `/sw.js` → `streakfit-v0924r`; `/health` 200;
   `/api/verification/self` at its baseline; `/admin` queue loads.
4. Record it in the release audit. Rolling forward again later is a normal
   deploy of the fixed release; its upgrade is a no-op.

If the release has to be abandoned for a longer time, `ef3178e` can stay
deployed indefinitely: its models match the schema, and the migration-parity
test passes on it.

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

Third review (2026-09-24), decided and built:

- **Open-ended block:** `/admin` marks a report the first time it holds up an
  account deletion (no id, no role; the 409 unchanged). Legal holds extending
  the block, and the block having no limit, are documented in
  privacy-retention.md.
- **Refusal wording:** no longer implies a route that does not exist.
- **Rollback:** by code (`ef3178e`), not by downgrade (section 2). The
  migration's downgrade is unchanged and drops its columns.
- **Challenge evidence:** `target_left: true` when its target has gone.

Rollback-build test (2026-09-24), found and NOT fixed in this release —
both pre-date it, in `4979354` too:

- **An expired challenge can still be completed** by a direct request:
  `complete_team_challenge` never checks `expires_at` (the card hides it).
  One guard (`if not _challenge_is_open(challenge): 404/409`) would close it;
  left for a follow-up so this release changes no challenge behaviour.
- **A closed appeal can be decided again** (outcome and note overwritten,
  the lifted restriction stays lifted): `admin_decide_appeal` checks for a
  missing appellant but not `status == 'open'`. Operator-only.

Still open, for the owner:

- ~~A reporter can unlink themselves from a pending child_safety report~~ —
  **decided and built:** a reporter's deletion is refused (generic 409) while
  any report they filed is pending; it ends when the report is decided,
  unless a hold remains.
- ~~An expired challenge to a deleted person reads "challenged the team"~~ —
  **fixed:** `team_challenge.target_left_at` (in the same migration) makes the
  card read "challenged a former teammate".
- **Deadlock retry costs ~1 s** per colliding pair; a stable lock order across
  shared rows would avoid it. Latent while production runs one worker.
