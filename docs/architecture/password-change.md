# Password change — design

**Status: design only. Not implemented.** Written 2026-10-12 against
production `6b6e1cd`.

**Why:** today a password is written once, at registration (`register()`).
Nothing can change it. If a password leaks, the only way to "rotate" it is to
delete the account. Access tokens are HS256 JWTs that last 1 hour, with no
refresh token and no revocation (`docs/architecture/authentication.md`). So a
stolen token stays valid for up to an hour whatever the owner does.

Password *recovery* (a forgotten password) is a separate design:
`password-recovery.md`. It depends on the session-invalidation mechanism here.

---

## 1. API

`POST /api/me/password`, with `@jwt_required()`.

Request body:

```json
{"current_password": "…", "new_password": "…"}
```

| Outcome | Status | Body |
|---|---|---|
| Changed | 200 | `{"changed": true, "access_token": "<new token>"}` |
| Malformed body or a non-string field | 400 | `{"error": "invalid_request"}` (the same helper as login, `_credential_fields`) |
| `current_password` wrong or missing | 403 | `{"error": "password_incorrect"}` |
| New password fails the policy (§2) | 400 | `{"error": "password_policy", "reason": "<code>"}` |
| Rate-limited | 429 | the existing handler |
| Deleted account | 404 | as every other `/api/me` route |

Steps, all in one transaction:

1. `_lock_user(user_id)`. Concurrent changes, and a change racing an account
   deletion, serialise on the same row lock every economy write uses.
2. Check `current_password` against the stored hash. A failure is charged to
   the throttle (§4), and nothing else happens.
3. Check the policy. A failure is not charged.
4. Write `password_hash`, `password_changed_at = server_now()` and
   `token_version = token_version + 1`. Commit.
5. Return a fresh token carrying the new `token_version`. The device that made
   the change stays signed in; every other device is signed out on its next
   request (§3).

**Why the current password is required even with a valid token.** A token
left on a shared family tablet, or a stolen one, must not be enough to take
the account over. Account deletion already applies the same rule.

## 2. Password policy

This follows NIST SP 800-63B and is consistent with what registration already
enforces:

- **Length:** 8–128 characters, the same bounds as `register()`. The upper
  bound exists so a huge input can't make the hasher a denial-of-service.
- **Different from the current password**
  (`check_password_hash(old_hash, new) is False`).
- **Not the username,** compared case-insensitively.
- **Not on a common-password list.** A small static file in the repo (e.g.
  the 10,000 most common passwords), checked offline. Nothing is sent to a
  third-party breach API.
  - This is a content decision: the list's source and licence need choosing.
  - Until a list is chosen, the policy ships without this check rather than
    with a made-up list.
- **No composition rules** (forced symbols or digits) and **no periodic
  expiry.** NIST advises against both: they push people toward predictable
  patterns.
- **Hashing:** unchanged (`pbkdf2:sha256` via Werkzeug, as registration).
  Moving to scrypt or argon2 with rehash-on-login is a separate item.

The same policy should then also apply at registration. That changes
registration behaviour (common passwords become refused), so it is a separate
owner decision.

## 3. Session invalidation

**Mechanism: a per-account token version.**

- Add `user.token_version INTEGER NOT NULL DEFAULT 0` (`server_default='0'`,
  so the additive migration needs no backfill).
- Every token carries `tv`:
  `create_access_token(identity=str(user.id), additional_claims={"tv": user.token_version})`.
  That covers login, registration auto-login if added, and the password-change
  response.
- Register `@jwt.token_in_blocklist_loader`. It loads `token_version` for the
  token's user (one indexed primary-key read) and reports the token as revoked
  when `claims.get("tv", 0) != user.token_version`.
- Register `@jwt.revoked_token_loader`. It returns
  `401 {"error": "session_ended"}`, which the client shows as "You were signed
  out because your password changed — sign in again."
- **A missing user is not a revoked token.** The loader returns "not revoked",
  and the route answers 404 exactly as now. This keeps the deletion smoke
  contract (old token → 404) and every existing test unchanged.

Considered and rejected:

| Alternative | Why not |
|---|---|
| Compare token `iat` with `password_changed_at` | Same cost per request. Second-granularity edge cases. Can't add "sign out everywhere" without a password change. |
| A denylist of token IDs (`jti`) | Needs storage of every live token, or a shared cache. The version check needs neither. |
| Shorter tokens plus a refresh token | A larger change to the client. It doesn't revoke the token already stolen. |

**Cost:** one primary-key read per authenticated request. Most routes already
load the user, and production is one sync worker reading Neon.

- Measure before release with `verify_all` timings.
- If it ever matters, the loader can share the request's `User` load, through
  a request-scoped cache on `g` that `_lock_user` refreshes.

**Compatibility:**

- Tokens issued before the deploy have no `tv`. They are read as 0, so they
  match every account until that account's first password change. They expire
  within the hour regardless.
- Old builds ignore the extra claim. A rollback after deploy therefore needs
  no token handling. It only loses revocation, back to today's behaviour.

The same mechanism gives "sign out everywhere" (increment without a password
change) and is what recovery (`password-recovery.md`) uses.

## 4. Rate limiting

Keyed on the user (`user_or_ip_key`), like account deletion:

- `5 per 15 minutes` on **wrong current password**
  (`deduct_when=status == 403`). This limits guessing with a stolen token.
- `10 per day` on all attempts, which bounds churn.
- `@sensitive_when_degraded("strict")`, like login: it keeps working with a
  tight per-process cap if the shared rate-limit store is down.

## 5. Audit logging, without sensitive values

- On success:
  `event=password_changed user_id=<id> sessions_revoked=true`.
- On refusal:
  `event=password_change_refused user_id=<id> reason=current_incorrect|policy:<code>|rate_limited`.

Never logged: either password, its length, a hash, a token, the policy word
list entry that matched, or the request body.

- `hide_parameters=True` already keeps SQL parameters out of errors.
- A test asserts that no log record contains the submitted passwords.

The data export gains `password_changed_at` (a timestamp, not a secret) so a
person can see when it last changed.

No new table is needed. If an audit *table* is wanted later, it needs a rule
in `_user_fk_classification`; `test_every_foreign_key_into_user_has_a_deletion_rule`
will fail until it has one.

## 6. Account deletion compatibility

- Deletion still re-checks the password. After a change, the **new** password
  is required, and the old one is refused.
- A token revoked by the change cannot reach `DELETE /api/me` at all (401
  `session_ended`), so a thief holding an old token cannot destroy the account
  after the owner changes the password.
- The new columns live on the `user` row and go with it. No new foreign key
  is added.
- A change racing a deletion serialises on the row lock. Either the change
  commits first and deletion then needs the new password, or deletion commits
  first and the change answers 404.

## 7. Migration and rollback

- **One additive Alembic revision:** `token_version` (NOT NULL, server
  default 0) and `password_changed_at` (NULL).
  - `SET LOCAL lock_timeout = '5s'` in **both** upgrade and downgrade. Adding a
    column with a constant default is metadata-only on PostgreSQL 11+, but the
    statement still takes ACCESS EXCLUSIVE on `user` briefly. The Step 1
    rollback audit reproduced what happens without a timeout.
  - `tests/test_migrations.py` and the PostgreSQL variant must pass.
- **Rollback:** deploy the previous build first. It ignores the new claim and
  columns. Then downgrade, which drops the two columns. The ordering is safe
  either way, because no old code reads the columns.
- **Release:** the change needs no client change to be safe. The client adds
  the Settings form and handles `session_ended`. Bump `static/sw.js`.

## 8. Tests required

| # | Case | Expected |
|---|---|---|
| P1 | Correct current password, valid new password | 200 and a new token; login with the old password → 401, with the new → 200 |
| P2 | Wrong current password | 403, nothing changed, charged; 5 in 15 min → 429 |
| P3 | **Stolen token, no password** (attacker has only the token) | Cannot change: 403 every time, throttled after 5 |
| P4 | **Another device's token after a change** | 401 `session_ended` on every route, including `DELETE /api/me` |
| P5 | The changing device's new token | Works |
| P6 | **Expired token** (clock moved past `exp`) | 401 "Token has expired"; cannot change |
| P7 | Pre-feature token (no `tv`) | Works until the first change, then 401 |
| P8 | Policy: too short, too long, same as current, equals username (any case), common list | 400 `password_policy` with the right code; not charged |
| P9 | Malformed bodies (array, numbers, lists) | 400 |
| P10 | Deleted account's token | 404 (unchanged) |
| P11 | Logs during P1–P9 | No record contains either password or a token |
| P12 | Concurrent change and deletion (PostgreSQL) | No 500. Either order is consistent (§6). |
| P13 | Deletion after a change | Old password 403, new password 200 |
| P14 | Data export | Includes `password_changed_at`, never a hash |
| P15 | Migration chain (SQLite + PostgreSQL); downgrade then upgrade | Schema equals models |
| P16 | `verify_all` module: change on a throwaway `qa_smoke_*` account, old token refused, new token accepted, then the account deleted | Production-safe end-to-end check |

## 9. Owner decisions

1. Approve the token-version mechanism, including the one read per request.
2. Choose the common-password list (source and licence), or ship without it.
3. Apply the policy to registration too? This refuses common passwords for
   new accounts.
4. Add "sign out everywhere" to Settings? It's the same mechanism.
