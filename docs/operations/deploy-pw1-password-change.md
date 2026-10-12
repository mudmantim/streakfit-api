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
5. **The rollback build is ready:** `rollback/pw1-compat` is pushed, CI is green on it, and its SHA is recorded (Rollback A).
6. **A fresh backup**, because this release changes the schema. Use the
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

## Rollback

There are two procedures. **Use A.** B exists for the case where A's
prerequisites aren't met, and it causes an outage.

### A. Zero-downtime rollback: deploy the compatibility build (preferred)

**What it is:** branch `rollback/pw1-compat`, commit **`7a7fee3`** (local;
not yet pushed). That is production `1d7b2f2` plus:

- the PW-1 migration file, byte-identical, so its Alembic head is
  `e8b3c1f0a7d2`;
- the two columns declared on the model;
- **PW-1's session security, verbatim:**
  - every token is stamped with the account's `token_version` (`tv`);
  - every authenticated route refuses a token whose version has moved
    (`401 session_ended`);
  - a deleted account's token still gets its 404;
- PW-1's password verification (NFKC, raw, and for non-ASCII input NFC/NFD),
  so passwords set under PW-1 and before it both sign in;
- malformed (unencodable) credentials answered 400.

It does **not** have `POST /api/me/password`, and no code in it writes
`token_version`. So nothing in it can revoke a session, and nothing can
un-revoke one.

**Its own `STREAKFIT_ENFORCE_DB_HEAD` check passes against the PW-1 database
as it stands:**

- nothing is disabled;
- nothing is downgraded;
- no request meets a missing column.

*History:* the first compatibility build (`f50ac70`) ignored `tv`, so
sessions revoked by a PW-1 change worked again for up to an hour while it
served. That was rejected at the security gate; `1a286d6` fixed it.

**Prerequisites, all before PW-1 is released:**

1. `rollback/pw1-compat` is pushed, and **both CI jobs are green on it**.
   Record its full SHA next to the PW-1 release SHA.
2. Production's database is at `e8b3c1f0a7d2`, and **no later migration has
   been deployed**. The compatibility build refuses any other head.
3. Render Auto-Deploy is Off (manual deploys by SHA).

**Procedure:**

1. Render → Manual Deploy → Deploy a specific commit → the compatibility SHA.
2. Watch the log:
   - Pre-Deploy `flask db upgrade` prints **no** "Running upgrade";
   - `Database migration check passed (at head e8b3c1f0a7d2)`;
   - one worker;
   - "Deploy succeeded".
3. Check:
   - identity shows the compatibility SHA, `e8b3c1f0a7d2`, `atHead: true`,
     `appliedCount` 29;
   - `/health` 200;
   - **and a real sign-in**, with a positive control. `/health` does not
     touch the user table.
4. Later, roll forward by deploying a fixed PW-1 build. No migration either
   way.

**Rehearsed on disposable PostgreSQL 17.10, 2026-10-12**
(`scripts/rehearse_pw1_rollback.py`):

- the head check is on at every boot;
- the script checks its own expectations and exits 1 on any failure;
- **0 failures**.

| Phase | Observed |
|---|---|
| PW-1 | Two people changed their password, one to a full-width password. Their second devices got 401 |
| PW-1 → compat | Pre-Deploy: no upgrade. Boot check passed. **Revoked sessions refused on all 44 authenticated routes** (URL-map walk, `401 session_ended`), including `DELETE /api/me` and `/api/daily`. New passwords sign in (the full-width one typed either way); old ones 401. PW-1 tokens keep working. Compat tokens carry `tv = 1`. `/api/me/password` 404. Versions untouched |
| compat → PW-1 | No upgrade; boot check passed. **Compat sessions still valid (no forced sign-out).** The revoked tablet still refused. A new change revokes the compat session |
| Failure conditions | The compat build refuses to start on a downgraded head and on a newer head |

**Also run against the compatibility build:**

- the full test suite;
- the PostgreSQL CI steps;
- `verify_all` v15 while serving with the head check on.

Results are in the release record.

**Limitations:**

- **No password change** while it serves. Pages loaded on PW-1 show the form,
  which gets 404 → "Couldn't change the password just now."
- **Registration uses production `1d7b2f2`'s rules** (8+ characters, no
  blocklist) while it serves. This is an owner decision: the compatibility
  build could carry PW-1's registration policy too, at the cost of being
  further from the known-good build.
- **No one-time NFKC rehash** at sign-in (verification still accepts every
  form). PW-1 does the rehash after roll-forward.
- **Old cached pages** show "Session expired" instead of the
  password-change wording on a `session_ended` 401. Same effect: the
  sign-in screen.

**Failure conditions and recovery:**

| Condition | What happens | Recovery |
|---|---|---|
| The database isn't at `e8b3c1f0a7d2` (someone downgraded, or a later migration was deployed) | The compatibility build **refuses to start** (`Refusing to start: database is at Alembic revision …`). Rehearsed for both. Render keeps serving the previous instance when a new one fails to boot (verify on staging before relying on it) | Don't force it. Deploy a build whose head matches the database, or use B |
| CI hasn't run on the compatibility SHA | Unknown build quality | Push it and wait for green **before** the PW-1 release (prerequisite 1) |
| Sign-in fails after the deploy although `/health` is 200 | Not expected (rehearsed); treat it as an incident | Redeploy PW-1 (same head, no migration), then investigate |

### B. Downgrade-first rollback (fallback only): **this causes an outage**

Use B only if A's prerequisites aren't met, e.g. the compatibility build was
never pushed.

1. From a trusted machine with production's `DATABASE_URL` in the
   environment (never in argv or chat):

   ```
   PGOPTIONS='-c lock_timeout=5s' flask db downgrade d4f1b6e2a9c3
   ```

   If it stops on `lock timeout`, retry. Nothing is half-applied (rehearsed:
   `test_a_held_row_lock_makes_the_rollback_fail_fast_and_then_it_succeeds`).
2. **Immediately** deploy `1d7b2f2d48b36beccc21f09cfc88675cac11629a` by SHA.
   Its head check needs `d4f1b6e2a9c3`, and would refuse `e8b3c1f0a7d2`
   (rehearsed: `test_the_head_check_refuses_a_build_that_does_not_know_the_revision`).

**⚠ Between steps 1 and 2 the still-serving PW-1 build returns HTTP 500 for:**

- **sign-in**;
- **registration**;
- **every signed-in request**.

Its model selects columns that no longer exist. **Meanwhile `/health`,
`/api/health` and the demo still answer 200, so Render's health check stays
green.** The outage is invisible to health monitoring and lasts as long as
the redeploy: build, Pre-Deploy and boot, so minutes.

Rehearsed on PostgreSQL (`scripts/rehearse_pw1_rollback_window.py`):

| Requests | Status |
|---|---|
| login, register, `/api/me`, `/api/daily` | 500 |
| `/health`, `/api/health`, `/api/demo/daily` | 200 |

Monitor with a real sign-in, not `/health`.

**B also:**

- drops `token_version` and `password_changed_at` (the export loses that
  field);
- a later roll-forward resets every version to 0, which re-validates tokens
  revoked within the last hour and signs out once the holders of `tv ≥ 1`
  tokens.

## Watch afterwards

- `event=password_changed` / `event=password_change_refused reason=…`.
  These carry `user_id` and a reason code only.
- `session_ended` 401s should follow password changes, and nothing else.
  A burst without matching `password_changed` events is a bug.
