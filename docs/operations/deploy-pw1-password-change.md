# Deploying PW-1 (password change + sign-out everywhere)

**Status: not released.** It is on a local branch for review. Every step
here is the owner's.

- Design: `docs/architecture/pw1-implementation-plan.md`.
- Code: `POST /api/me/password`, `_token_revoked`, `_issue_access_token`.

## What changes

| Area | Change |
|---|---|
| Schema | Migration `e8b3c1f0a7d2` (revises `d4f1b6e2a9c3`), additive. It adds `user.token_version INTEGER NOT NULL DEFAULT 0` and `user.password_changed_at TIMESTAMP NULL`. Existing rows read 0 / NULL. No backfill, no rewrite (PostgreSQL 11+). |
| Tokens | Login tokens carry a `tv` claim. Every authenticated request compares it with `user.token_version`; a mismatch is `401 session_ended`. Tokens from the previous build have no `tv`, which reads as 0, so **nobody is signed out by the deploy**. |
| New route | `POST /api/me/password {current_password, new_password}`. Results: 200 + new token; 403 wrong current password; 400 policy or malformed body; 429 throttled. |
| Throttles | 5 wrong current passwords per 15 minutes, and 10 attempts of any kind per day, both per user. Per-process strict cap while the shared store is degraded. |
| Cost | One primary-key read of `user` per authenticated request. It is kept for the request (`g._sf_auth_user`), so a route that loads the caller itself (`/api/me`, `/api/daily`, the export) issues **no** extra statement. Routes that never load the caller (team list, team detail, messages, moments) issue **one** more than before, constant with data size. Measured against `1d7b2f2`; see `tests/test_password_change.py`. *(Corrected: an earlier version claimed the identity map alone avoided the second read. It does not, because it holds objects weakly.)* |
| Client | Settings → Password → Change password. A `session_ended` 401 shows "Your password was changed…" on the sign-in screen. `sw.js` v1008. |
| Export | `account.password_changed_at`. |
| Verification suite | v16 adds 7 `auth.password_*` checks on the throwaway leaver account. |
| Password policy | **New passwords only** (registration and change), one shared policy (`docs/security/password-policy.md`):<br>• 15+ characters<br>• not mostly the username<br>• not the service name<br>• not repetitive or sequential<br>• not on the breached/common blocklist (`docs/security/password-blocklist.md`)<br>Registration's 400 keeps `error` as the human sentence and adds `code`. |
| Unicode | New passwords are hashed in NFKC. Sign-in, deletion and the current-password check try NFKC, the raw string, and (non-ASCII only) NFC/NFD, then upgrade the hash once, under the row lock. **No existing password stops working.** |
| Unchanged | **Existing accounts' access:** a password set under the old 8-character rule keeps signing in. Also unchanged: login responses, deletion (a deleted account's token is still 404), JWT expiry (1 h). |
| Verification suite | v18. `SMOKE_PASSWORD` is now 15+ characters, and it still works against a pre-PW-1 build. New check: `auth.password_common_is_refused`. |

## Preconditions

1. Production identity shows the expected previous SHA, `d4f1b6e2a9c3`,
   `atHead: true`.
2. Auto-Deploy is Off. One instance.
3. Outside 23:30–00:30 UTC.
4. Both CI jobs are green on the exact SHA. The `postgres` job now includes
   `test_password_change_postgres.py`.
5. **A fresh backup**, because this release changes the schema. Use the
   custody scripts; the 14-day deadline is printed.

## Deploy

1. Fast-forward `main` to the reviewed SHA.
2. Render: Manual Deploy → the exact SHA.
3. Watch the log:
   - Pre-Deploy shows `Running upgrade d4f1b6e2a9c3 -> e8b3c1f0a7d2`;
   - then `Database migration check passed (at head e8b3c1f0a7d2)`;
   - one worker.
   - The migration takes a brief ACCESS EXCLUSIVE lock on `user`, with
     `lock_timeout = 5s`. If a held row lock makes it fail, the deploy stops
     with the old build still serving. **Retry; don't remove the timeout.**
     (Rehearsed: `test_the_upgrade_also_fails_fast_behind_a_held_row_lock`.)
4. Read-only checks:
   - identity: `e8b3c1f0a7d2`, `atHead`, `appliedCount` 29;
   - `/health` 200;
   - no Traceback or 5xx, with a positive control.
5. Owner-approved: `verify_all` v16. It changes the password of its own
   throwaway account only.

## Rollback: downgrade FIRST, then redeploy

The previous build runs with `STREAKFIT_ENFORCE_DB_HEAD=1` and **refuses to
boot** against `e8b3c1f0a7d2`. That is reproduced in
`test_the_head_check_refuses_a_build_that_does_not_know_the_revision`.

1. From a trusted machine, with the production `DATABASE_URL` in the
   environment (never in argv or chat):

   ```
   PGOPTIONS='-c lock_timeout=5s' flask db downgrade d4f1b6e2a9c3
   ```

   - If it stops on `lock timeout`, retry. Nothing is half-applied
     (rehearsed: `test_a_held_row_lock_makes_the_rollback_fail_fast_and_then_it_succeeds`).
   - From this moment the still-serving PW-1 build reads columns that are
     gone, so authenticated requests fail. **Do step 2 immediately.**
2. Render: Manual Deploy → the previous SHA.
3. Check:
   - identity shows the previous SHA, `d4f1b6e2a9c3`, `atHead: true`;
   - `/health` 200.

## What rollback does not undo (limitations)

- **Changed passwords stay changed.** The hash format is identical, so
  anyone who changed their password logs in with the new one on the old build.
- **Sign-outs stop.** The old build ignores `tv`. A token that a change had
  revoked becomes valid again until it expires (at most 1 hour after it was
  issued). Someone who changed their password *because* a device was lost is
  exposed for that hour. **Prefer roll-forward** for anything short of an
  outage.
- `password_changed_at` values are dropped by the downgrade, and the export
  loses that field.
- Pages loaded on the PW-1 build show a "Change password" button that 404s
  on the old build until they reload. The form shows "Couldn't change the
  password just now."
- Between steps 1 and 2 authenticated requests fail. The gap is as long as
  the redeploy takes.

## Watch afterwards

- `event=password_changed` / `event=password_change_refused reason=…`.
  These carry `user_id` and a reason code only.
- `session_ended` 401s should follow password changes, and nothing else.
  A burst without matching `password_changed` events is a bug.
