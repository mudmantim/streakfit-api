# PW-1: password change with sign-out everywhere — implementation plan

**Status: implemented on branch `feature/pw1-and-child-safety-phase0`. Not
released.** Written 2026-10-12 against release candidate `1d7b2f2`. The
runbook is `docs/operations/deploy-pw1-password-change.md`.

**Where the implementation differs from this plan, and why:**

1. **Two slow hashes, not three.** "Same as current" is checked by comparing
   the two submitted strings, before any hashing. Once the current password
   is verified, a new one that differs from what was typed also differs from
   what is stored. `test_a_successful_change_hashes_exactly_twice` pins this.
2. **The `g` cache is needed after all,** and is now implemented. The first
   version relied on the identity map alone and claimed routes would reuse
   the loader's read. They did not: the identity map holds objects weakly, so
   the instance was collected and `/api/me` read the row twice. The review
   measured this (`/api/me` 4 → 5 statements). The loader now keeps the row on
   `g` for the request.
3. **Policy:** `equals_username` became `contains_username` (usernames of 4+
   characters). Also added, all from NIST SP 800-63B's own examples and
   needing no list:
   - `service_name` (only "streakfit"/"rickie" once digits and symbols are
     dropped);
   - `repetitive` (one unit of 1–3 characters repeated);
   - `sequential` (a straight run of keys).

   The common-password list is still empty pending the owner's decision.
4. **The rollback order in §6 was corrected:** downgrade first. That is now
   reproduced by a test.

- It narrows `password-change.md` and `password-implementation-checklist.md`
  (findings F1–F7, test matrix T1–T16) to one small release.
- Recovery codes (PW-2) and email are out of scope.

## Current state (verified in `app.py` at `1d7b2f2`)

**Tokens:**

- `create_access_token(identity=str(user.id))` is called in one place, login.
- JWT HS256, 1-hour expiry.
- No refresh token, no `token_in_blocklist_loader`, no token version.
- **Consequence:** a token stays valid for up to an hour, whatever happens to
  the password.

**Passwords:**

- Werkzeug `pbkdf2:sha256` (600,000 iterations), 165 ms per check measured.
- Registration requires 8–128 characters. Login and delete have no length
  check before hashing; the body is capped at 256 KB, and PBKDF2 pre-hashes
  long keys, so the cost doesn't grow with length.

**Building blocks that already exist:**

- `_lock_user(user_id)` (row lock, re-reads the user);
- `_credential_fields(...)` (S4, rejects non-strings);
- `user_or_ip_key`;
- `sensitive_when_degraded`;
- `deduct_when` throttles (the login pattern).

## Scope of PW-1 (one release)

1. Token version, so a change signs out every other device.
2. `POST /api/me/password`.
3. A Settings form, and global handling of `session_ended`.

## 1. Current-password verification

`POST /api/me/password {current_password, new_password}`, `@jwt_required()`:

1. `fields = _credential_fields('current_password', 'new_password')`.
   - Malformed or missing → 400 `{"error":"bad_request"}`.
2. **Policy checks on `new_password`, with no hashing (F2):**
   - 8 ≤ length ≤ 128;
   - not equal to the username (case-insensitive);
   - not in the common list, if the owner supplies one.

   On failure → 400 `{"error":"password_policy","code":…}`. Cap
   `current_password` at 128 too (400) before any hash is computed.
3. `user = _lock_user(int(get_jwt_identity()))`.
   - No user → 404, the same as the deletion contract.
4. `check_password_hash(user.password_hash, current)`.
   - False → 403 `{"error":"current_password_wrong"}`. **Rollback, nothing
     written.**
5. `check_password_hash(user.password_hash, new)`.
   - True → 400 `{"error":"password_policy","code":"same_as_current"}`.
6. Write:
   - `password_hash = generate_password_hash(new, method='pbkdf2:sha256')`;
   - `password_changed_at = utcnow()`;
   - `token_version += 1`;
   - commit.
7. Return `200 {"access_token": create_access_token(identity=..., additional_claims={"tv": user.token_version})}`.

**Worst case:** three slow hashes, about 0.5 s on the single worker. It is
bounded by the throttles below and measured on staging before release.

**Why 403, not 401, for a wrong current password:** the client treats 401 as
"signed out". A mistyped current password must not sign the person out.

## 2. Token invalidation across devices

- **Migration (additive, one revision):**
  - `user.token_version INTEGER NOT NULL server_default '0'`;
  - `user.password_changed_at TIMESTAMP NULL`;
  - `SET LOCAL lock_timeout = '5s'` in both upgrade and downgrade. Both take
    a brief ACCESS EXCLUSIVE lock on `user`. Adding a column with a constant
    default is metadata-only on PostgreSQL 11+, so no table rewrite.
- **Login** issues `additional_claims={"tv": user.token_version}`. A grep test
  asserts that every `create_access_token` call passes `tv`.
- **Blocklist check:**

  ```python
  @jwt.token_in_blocklist_loader
  def _token_revoked(_header, claims):
      user = _request_user(int(claims["sub"]))       # PK read, cached on g (F3)
      if user is None:
          return False          # deletion contract: the route answers 404 (F4)
      return claims.get("tv", 0) != (user.token_version or 0)

  @jwt.revoked_token_loader
  def _session_ended(_header, _claims):
      return jsonify({"error": "session_ended"}), 401
  ```

- **Pre-release tokens:** they have no `tv`, which reads as 0 and equals the
  default. So everyone stays signed in through the deploy (T7).
- **Cost:** one primary-key read per authenticated request. `_request_user`
  caches it on `g`; `_lock_user` refreshes the cache. Compare `verify_all`
  timings before and after.
- **Client:**
  - store the returned token;
  - any `401 session_ended` → sign-in screen with "You changed your password
    on another device — please sign in again";
  - bump `static/sw.js` (SW cache rule).

## 3. Password policy

**Server-side, one helper:** `_password_policy_problem(username, new) -> code | None`.

| Rule | Code | Decided? |
|---|---|---|
| 8–128 characters | `too_short` / `too_long` | Yes (matches registration) |
| Not the username | `equals_username` | Yes |
| Not the current password | `same_as_current` | Yes (checked after the current-password check) |
| Not on a common-password list | `common` | **[OWNER]** list source and licence; ship without it rather than with an invented list |
| No composition rules, no expiry | — | Yes (NIST SP 800-63B) |
| Applies to registration as well | — | **[OWNER]**. Default: not in PW-1, so registration behaviour is unchanged |

## 4. Rate limiting

Pattern copied from login, keyed `user_or_ip_key`:

```python
@limiter.limit("5 per 15 minutes", key_func=user_or_ip_key,
               deduct_when=lambda r: r.status_code == 403)
@limiter.limit("10 per day", key_func=user_or_ip_key)
@sensitive_when_degraded("strict")
```

- Only wrong current passwords are charged at 5 per 15 minutes. Typos in the
  new password (400) cost nothing beyond the daily cap.
- A stolen token can't be used to grind the current password (T3).
- **Per user, not per IP,** so the operator's IP is not shared with the
  `verify_all` budget.
- With the store degraded, the per-process strict caps still apply. Never
  unthrottled.

**Logs:**

- `event=password_changed user_id=… sessions_revoked=true`;
- `event=password_change_refused user_id=… reason=<code>`;
- never a password, its length, a hash or a token. A test captures the log
  output (T13).

## 5. Regression tests

Matrix T1–T16 in `password-implementation-checklist.md`. The minimum set for
PW-1:

| Area | Tests |
|---|---|
| **Unit (SQLite)** | T1–T10, T13, T14; the grep test (`tv` on every token); the hasher spy for F2 (policy failure → zero hash calls) |
| **Existing suites unchanged** | `test_auth.py`, `test_login_throttle.py`, `test_account_deletion*.py`, `test_security_hardening.py`. The blocklist loader must not change any of them, especially deleted-account → 404 |
| **PostgreSQL** | T11 (change vs delete), T12 (two concurrent changes), T15 (migration up/down/up). Run by the CI `postgres` job once it is on `main` |
| **Verification suite** (CLAUDE.md standard) | Extend `scripts/verification/auth.py`, on a throwaway `qa_smoke_*` account: change; old token → 401 `session_ended`; new token works; old password → 401; delete with the new password. Bump the suite version |
| **Browser** | T16: Settings change flow; a second tab gets `session_ended` and shows the sign-in screen |
| **UI check** | Add the Settings form to `uicheck.py` at phone width |

## 6. Safe migration and rollback

**Deploy order:**

1. Pre-Deploy `flask db upgrade` adds the two columns.
2. The new code boots.
3. `STREAKFIT_ENFORCE_DB_HEAD=1` passes.

The old code, still serving during the deploy, ignores the extra columns. The
`user` inserts get the server default 0.

**Rollback: downgrade first, then redeploy** (the same order as Step 1).

- The previous build runs with `STREAKFIT_ENFORCE_DB_HEAD=1`, which refuses to
  boot when the database is at a revision the code doesn't know. So the
  database must be back at the old head before the old build starts.

1. **Downgrade one revision** with `PGOPTIONS='-c lock_timeout=5s'`.
   - While the PW-1 build is still serving, it loses the columns it reads, so
     authenticated requests fail until step 2.
   - Keep the gap short. Do it outside 23:30–00:30 UTC.
2. **Immediately redeploy the previous build** by full SHA.
3. Check:
   - identity shows the old SHA and the old head, `atHead: true`;
   - `/health` 200.

Rehearse the whole sequence on a disposable PostgreSQL database before
release, and write it into the runbook.

**Correction to `password-implementation-checklist.md`:** it said "redeploy
the previous build, then downgrade if wanted". That order fails, because the
old build won't boot against the newer head.

**Rollback effects:**

- Revocation stops working. Tokens from other devices that a change had
  revoked become valid again until their 1-hour expiry.
- Changed passwords stay changed; the hash format is the same.
- The downgrade drops `password_changed_at` and `token_version`. That loses
  only the timestamps, with no user-visible effect.

**No data backfill.** The default covers every existing row.

## 7. Owner decisions before implementation

1. Approve PW-1 as specified.
2. Common-password list: source and licence, or none.
3. Apply the policy to registration (default: no, in PW-1).
4. The wording of the `session_ended` message.
