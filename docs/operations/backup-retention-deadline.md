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
  inside 30, not overridable from the environment); the four deadlines above;
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
