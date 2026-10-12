# Backup retention deadline

Encrypted production backups live outside the repository, in
`~/backups/streakfit/`, on purpose (they and their scripts touch the Neon
connection string and gpg passphrases). This file records the retention rule
those scripts print and every change made to them, by SHA-256.

## The rule

An encrypted production backup is deleted (`shred -u`) **14 days after the
UTC date it was taken, or as soon as the release it protects is verified —
whichever comes first.**

- Why it exists: a backup freezes data StreakFit promises to delete within 30
  days (coach conversations, moderation evidence — see `privacy-retention.md`).
  Holding a backup longer quietly voids that promise, because deletion in the
  live database does not reach it.
- Why 14 days: it covers a deploy and a week of verification (stated in
  `docs/operations/release-audit-2026-09-23.md` as it stands on the unmerged
  branch `integrate-product-completion` (`d889789`); the copy of that file on
  `main` predates the sentence). It bounds how long a *backup* lives, not how
  old the rows in it are: a conversation 29 days old when a backup is taken
  can survive in that backup up to 14 more days. Shortening the window narrows
  that; it is the owner's choice, not a defect.
- The rule is not new. It reproduces every deadline used so far:
  2026-09-21 → 2026-10-05, 2026-09-24 → 2026-10-08, 2026-10-03 → 2026-10-17.
  The owner's controlling form ("DELETE BY <date>, or as soon as the
  production migration is verified — whichever comes first") is in
  `e2e-campaign/evidence/retention/RETENTION_INVENTORY_2026-10-04.md`.
- Neon snapshots fall under the same rule. The only one ever taken
  (2026-09-21, set to expire NEVER) was deleted on 2026-10-04
  (`e2e-campaign/evidence/retention/RETENTION_CLEANUP_2026-10-04.md`).

## What was wrong (fixed 2026-10-12)

`streakfit-backup.sh` printed **"DELETE BY 2026-10-05"** for every backup —
the deadline of the 2026-09-21 backup the script was written for, hard-coded.
A backup taken on 2026-10-12 was told its deadline had already passed, which
makes the instruction easy to ignore. Its closing message also still described
the 2026-09-21 Neon snapshot as live, eight days after it was deleted.

## The fix

- New `~/backups/streakfit/streakfit-retention.sh` (sourced, never executed):
  the one place the deadline is computed. `streakfit_backup_delete_by EPOCH`
  prints the UTC date 14 days after the backup's UTC date. The 14 is a
  constant in the file, deliberately **not** read from the environment — a
  longer window would weaken the privacy promise, so changing it means editing
  this file, which is recorded here.
- `streakfit-backup.sh`:
  - takes one timestamp and derives both the file name and the deadline from
    it, so the two cannot disagree at midnight;
  - loads the rule and computes the deadline right after its error helpers,
    **before** the `PGURL` check and before any container, gpg or network step
    — a missing or broken rule stops the run instead of printing a wrong date;
  - prints `DELETE BY <computed date> (14 days after this backup, UTC), or as
    soon as the release it protects is verified — whichever comes first`;
  - says that no Neon snapshot exists after the 2026-10-04 cleanup, and that
    any future snapshot needs the same deadline.
  Nothing else changed: no connection, dump, encryption, verification or
  cleanup behaviour.
- New `~/backups/streakfit/test-streakfit-retention.sh`: the rule (14 days,
  not overridable from the environment); the three deadlines above plus the
  2026-10-12 02:11Z backup (→ 2026-10-26);
  first and last second of a UTC day; month, year and leap-day boundaries; the
  US DST change; four process timezones; rejection of non-numeric, negative,
  date-string and injection-shaped input; and `streakfit-backup.sh` statically
  (syntax, sources the rule, banner uses the computed date, no hard-coded date,
  no stale snapshot text, deadline computed before the `PGURL` check) plus two
  safe runs — without `PGURL` it refuses before podman/gpg/network and writes
  nothing (output pointed at a temp dir), and without the rule file it stops
  with a clear error. It never sets `PGURL` and never touches a backup.

### Custody record (SHA-256)

| File | SHA-256 |
|---|---|
| `streakfit-backup.sh` before (kept as `streakfit-backup.sh.orig-20261012`, mode 500) | `5376dd790e747fd1511ef34d683f25eca62e5aa0a9002554eb5420f15287856f` |
| `streakfit-backup.sh` after | `960ef3ccc10105700d34f023f02a4663e963174532de291d0209eb417f1dd827` |
| `streakfit-retention.sh` (new) | `fd307834710f18e66065db20c1dcc5eb6e30f839e336a39aa7cf56f7f7ea5838` |
| `test-streakfit-retention.sh` (new) | `eb6a56cc7a90c29f138602c76c04174387aec4686eab869736d3626b69822894` |

### Verification so far (2026-10-12)

- `bash -n` passes on all three files.
- The date arithmetic was checked directly with GNU `date` (the same
  expressions the library uses) and matches every expected deadline in the test
  file, including leap years, year rollover and four process timezones.
- **Not yet run: the test suite itself.** Executing scripts in the custody
  directory needs the owner (the assistant's attempt was refused by the
  permission guard). Run it once, as the owner:

  ```
  bash ~/backups/streakfit/test-streakfit-retention.sh
  ```

  It must end with `0 failed`. It does not ask for any secret.

## Backups present on 2026-10-12

| File | Taken (UTC) | Delete by (14-day bound) | Release it protects |
|---|---|---|---|
| `streakfit-neondb-production-20261012T012309Z.dump.gpg` | 2026-10-12 01:23 | 2026-10-26 | Step 1 (`6b6e1cd`), verified 2026-10-12 |
| `streakfit-neondb-production-20261012T021114Z.dump.gpg` | 2026-10-12 02:11 | 2026-10-26 | Step 1 (`6b6e1cd`), verified 2026-10-12 |

Under the rule's "whichever comes first", both are **due now**: the release
they protect has been verified. Rollback of Step 1 is by code and a schema
downgrade (see `deploy-step1-daily-mission.md`) and does not need them; they
are the disaster-recovery point only. Deleting them is the owner's decision
(`shred -u <exact path>`, one file at a time, then `ls` the path); nothing
here deletes them. Note from the 2026-10-04 cleanup: on this btrfs SSD,
`shred -u` removes the file but cannot prove the physical blocks were
overwritten; the residue is AES-256 ciphertext.

## Restoring or reproducing the change

Nothing in these files is secret, so they are reproduced here in full; the
SHA-256 values in the custody record above verify any copy.

**Undo** (back to the hard-coded banner): in `~/backups/streakfit/`,
`cp -p streakfit-backup.sh.orig-20261012 streakfit-backup.sh && chmod 700
streakfit-backup.sh`, then `sha256sum streakfit-backup.sh` must print the
"before" value. The library can stay; the old script never reads it.

**Reproduce** (custody directory lost or rebuilt): save the library below as
`streakfit-retention.sh` (mode 600) and the patch below as `backup.patch`,
then `cp streakfit-backup.sh.orig-20261012 streakfit-backup.sh && chmod 700
streakfit-backup.sh && patch streakfit-backup.sh < backup.patch` (the `.orig`
is mode 500, so the copy needs the `chmod` before patching). Verified
2026-10-12: this yields exactly the "after" SHA-256. Compare all hashes. The test file
is not reproduced here; it can be rewritten from the list in "The fix".

Known wording flaw, left unchanged because editing the file changes its
recorded SHA-256: the library's comment and one test name say 14 days "stays
inside" the 30-day promise. That is true of how long a backup lives, not of
how old the rows in it are (see "Why 14 days" above). Fix it the next time the
file is edited for another reason, and record the new hash here.

### `streakfit-retention.sh`

```bash
# streakfit-retention.sh -- sourced, never executed. The one place the
# backup-retention deadline is computed.
#
# THE RULE (owner-controlled; see the repo's
# docs/operations/backup-retention-deadline.md):
#   An encrypted production backup is deleted (shred -u) 14 days after the UTC
#   date it was taken, OR as soon as the release it protects is verified --
#   whichever comes first.
#
# Why 14: the backup freezes data StreakFit promises to delete within 30 days
# (coach conversations, moderation evidence). 14 days stays inside that
# promise while covering a deploy and a week of verification. It is the rule
# behind every deadline used so far: 2026-09-21 -> 2026-10-05 (the date this
# script used to print for EVERY backup), 2026-09-24 -> 2026-10-08,
# 2026-10-03 -> 2026-10-17.
#
# Deliberately NOT configurable from the environment: a longer window would
# quietly void the 30-day promise. Changing it means editing this file, which
# the custody record tracks by SHA-256.

streakfit_backup_max_days() {
    printf '%s\n' 14
}

# streakfit_backup_delete_by EPOCH
#   EPOCH: the backup's own timestamp, UNIX seconds (UTC).
#   Prints the delete-by date, YYYY-MM-DD in UTC. Independent of the process
#   timezone. Returns non-zero, printing nothing on stdout, for anything that
#   is not a plain non-negative integer.
streakfit_backup_delete_by() {
    local epoch="${1-}" day days
    if [[ ! "$epoch" =~ ^[0-9]+$ ]]; then
        printf 'ERROR: delete-by needs the backup time as UNIX seconds, got: %q\n' "$epoch" >&2
        return 2
    fi
    day="$(TZ=UTC date -u -d "@${epoch}" +%F)" || return 2
    days="$(streakfit_backup_max_days)"
    TZ=UTC date -u -d "${day} + ${days} days" +%F || return 2
}
```

### `backup.patch` (original → current `streakfit-backup.sh`)

```diff
--- streakfit-backup.sh.orig-20261012	2026-09-23 17:11:14.590119478 -0400
+++ streakfit-backup.sh	2026-10-11 22:37:44.282214273 -0400
@@ -88,7 +88,10 @@
 readonly IMAGE="${SFBK_IMAGE:-docker.io/library/postgres:17}"
 readonly DEST="${SFBK_DEST:-$HOME/backups/streakfit}"
 readonly RUNDIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
-readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
+# One instant for the file name and the retention deadline, so the two can
+# never disagree (e.g. a backup started at 23:59:59 UTC).
+readonly STAMP_EPOCH="$(date -u +%s)"
+readonly STAMP="$(date -u -d "@${STAMP_EPOCH}" +%Y%m%dT%H%M%SZ)"
 readonly BASE="streakfit-neondb-production-${STAMP}.dump.gpg"

 # Every libpq variable we are willing to carry over from the URL. Anything in
@@ -108,6 +111,18 @@
 die() { printf '%s\n' "ERROR: $*" >&2; exit 1; }
 note() { printf '%s\n' "$*"; }

+# Retention deadline, computed from this backup's own timestamp (it used to be
+# a hard-coded 2026-10-05, printed for every backup). The rule lives in one
+# sourced file beside this script; computed before anything connects, so a
+# missing or broken rule stops the run instead of printing a wrong date.
+readonly SFBK_LIBDIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
+# shellcheck source=streakfit-retention.sh
+. "${SFBK_LIBDIR}/streakfit-retention.sh" \
+    || die "cannot load ${SFBK_LIBDIR}/streakfit-retention.sh (the retention rule)"
+DELETE_BY="$(streakfit_backup_delete_by "$STAMP_EPOCH")" \
+    || die "could not compute the retention deadline"
+readonly DELETE_BY
+
 cleanup() {
     local rc=$?
     # A container only lingers if we were killed mid-run; --rm covers the rest.
@@ -324,13 +339,14 @@
 records, and verbatim coach conversations.
   * Keep it at mode 600 under $DEST. Never in the repo, Downloads, Desktop,
     or any cloud-synced directory.
-  * DELETE BY 2026-10-05, or as soon as the production migration is verified —
-    whichever comes first:  shred -u "$FINAL"
+  * DELETE BY ${DELETE_BY} ($(streakfit_backup_max_days) days after this backup, UTC), or as soon as
+    the release it protects is verified — whichever comes first:
+        shred -u "$FINAL"
   * This file freezes data StreakFit promises to delete after 30 days. Holding
     it longer quietly voids that promise.
-  * THE NEON SNAPSHOT NEEDS THE SAME DECISION. The manual snapshot taken on
-    2026-09-21 18:07:41 UTC is set to expire NEVER, and is a permanent frozen
-    copy of the same data until someone removes it.
+  * NEON SNAPSHOTS NEED THE SAME RULE. None existed after the 2026-10-04
+    cleanup. A manual snapshot set to expire NEVER is a permanent frozen copy
+    of this data: if one is ever taken, give it the same deadline.
   * Losing the passphrase makes this file unrecoverable. There is no reset.

 Remember: unset PGURL
```
