# Password change and recovery codes — review, implementation checklist, test matrix

**Status: plan only.** No password change or recovery code exists in code yet.
Written 2026-10-12 after reviewing `password-change.md` and
`password-recovery.md`.

- **No email provider, and no email or phone collection.** StreakFit's "we
  hold no contact details" property is preserved.
- Option R2 (email reset) stays out of scope until the owner decides.

---

## 1. Review findings (design corrections)

| # | Finding | Correction |
|---|---|---|
| F1 | **Recovery codes were to be stored with the password hash.** That is `pbkdf2:sha256` with 600,000 iterations: measured 165 ms per check on this laptop under load, and slower on Render's starter plan. The design checks up to 10 codes per attempt, so **~1.7 s of CPU per request on production's single sync worker**. Unauthenticated, that makes every recovery attempt a denial-of-service amplifier for everyone. | Codes carry about 60 bits of entropy, so they don't need slow hashing. Store `HMAC-SHA256(RECOVERY_CODE_PEPPER, normalised_code)` and look it up **directly** (indexed column, one query, constant time). A new secret env var `RECOVERY_CODE_PEPPER`, with a self-check that it is set. Losing the pepper invalidates the codes, which is acceptable: people regenerate them. |
| F2 | A password change does up to three slow hash operations (check current, check "differs from current", generate new): about 0.5 s on the single worker. | Acceptable, bounded by the throttles (5 failures per 15 min, 10 per day). Order the checks so a policy failure costs **no** slow hash: length, username and the common list first, then the current-password check, then "differs". Measure on staging before release. |
| F3 | `token_in_blocklist_loader` adds a primary-key read to **every** authenticated request. | Keep it, but cache the user row on `g` for the request. `_lock_user` refreshes it (it already re-reads under the lock), so routes that load the user anyway pay nothing extra. Measure with `verify_all` timings before and after. |
| F4 | The design says a missing user reads as "not revoked", to keep the deletion contract (old token → 404). | Keep. `verify_all` `auth.deleted_account_*` and the Step 1 smoke script depend on it. |
| F5 | The recovery endpoint is unauthenticated and takes a username. Registration already reveals whether a username is taken, so uniform responses can't remove enumeration; they only avoid adding a second, faster channel. | Same status and body for an unknown username and a wrong code, plus a dummy HMAC lookup. Per-username failure limits must lock **recovery only**, never login. |
| F6 | Guardian-created child accounts (`child-safety/phased-plan.md` phase 3) need recovery without contact details. | Add later: the linked guardian resets the child's password, under the same lock and token-version rule. Not in this milestone. |
| F7 | Logs: a username typed into recovery can be an email address. | Recovery refusal lines carry no username. Success lines carry `user_id` only. |

---

## 2. Implementation checklist

Milestone **PW-1**: password change plus token version. One release.

- [ ] Migration (additive):
  - `user.token_version INTEGER NOT NULL server_default 0`;
  - `user.password_changed_at TIMESTAMP NULL`;
  - `SET LOCAL lock_timeout='5s'` in upgrade **and** downgrade;
  - `tests/test_migrations.py` and `tests/test_migrations_postgres.py` green.
- [ ] Every `create_access_token` adds `additional_claims={"tv": user.token_version}`.
      Today that's login only; also any future token issuer.
- [ ] `@jwt.token_in_blocklist_loader`: revoked when `claims.get("tv", 0) != user.token_version`.
      A missing user is **not** revoked.
- [ ] `@jwt.revoked_token_loader` → `401 {"error": "session_ended"}`.
- [ ] Request-scoped user cache on `g` (F3), refreshed by `_lock_user`.
- [ ] `POST /api/me/password`:
  - `_credential_fields('current_password', 'new_password')`;
  - `_lock_user`;
  - policy checks first (F2), then current, then "differs";
  - write hash, `password_changed_at`, `token_version + 1`;
  - commit;
  - return a new token.
- [ ] Policy helper `_password_policy_problem(username, new)`, shared with
      registration but **not applied there** until the owner decides.
      - Common-password list: **owner decision** on source and licence. Ship
        without it rather than with an invented list.
- [ ] Limits:
  - `5 per 15 minutes` deducted on 403;
  - `10 per day`;
  - key `user_or_ip_key`;
  - `@sensitive_when_degraded("strict")`.
- [ ] Logs: `event=password_changed user_id=… sessions_revoked=true`;
      `event=password_change_refused user_id=… reason=…`.
      **Never** the passwords, their lengths, hashes or tokens.
- [ ] Data export: `password_changed_at`.
- [ ] Client:
  - Settings form (current, new, confirm);
  - store the returned token;
  - global handling of `401 session_ended` → sign-in screen with an
    explanation;
  - bump `static/sw.js`.
- [ ] Verification suite: a module or extension of `auth.py`, on a throwaway
      `qa_smoke_*` account:
  - change;
  - old token refused;
  - new token accepted;
  - old password refused;
  - delete with the new password.

  Bump the suite version.
- [ ] Runbook entry. Rollback: redeploy the previous build (it ignores `tv`
      and the columns), then downgrade if wanted.

Milestone **PW-2**: recovery codes. Separate release, after PW-1.

- [ ] Migration: table `recovery_code`:
  - `id`, `user_id` FK, `code_hmac` (unique, indexed), `created_at`, `used_at`;
  - deletion rule in `_USER_PRIVATE_DELETES` (the FK-classification test
    forces it);
  - export lists the count and creation date, never the codes.
- [ ] `RECOVERY_CODE_PEPPER` env var; `/api/verification/self` reports
      "configured" (never the value).
- [ ] `POST /api/me/recovery-codes`:
  - authenticated;
  - **requires the current password**;
  - returns 10 codes once, 12 characters from an unambiguous alphabet;
  - replaces the previous set.
- [ ] `POST /api/recover {username, recovery_code, new_password}`:
  - HMAC lookup (F1);
  - same answer for an unknown username or a wrong code (F5);
  - on success: code used, new password, `token_version + 1`, new token.
- [ ] Limits:
  - per IP: 5/hour on 401, 20/day;
  - per username: 10 failures/day, recovery only;
  - `@sensitive_when_degraded("refuse")`.
- [ ] Logs per F7.
- [ ] Client:
  - "Make recovery codes" in Settings with a print/save view;
  - "Forgot password? Use a recovery code" on sign-in.
- [ ] Verification-suite checks:
  - generate;
  - recover;
  - reuse refused;
  - throttle answers 429 (with care: verify_all shares the operator's IP, so
    use few attempts).

---

## 3. Test matrix

**Legend:**

- **U** = pytest on SQLite;
- **PG** = pytest on disposable PostgreSQL;
- **V** = `verify_all` against a local server;
- **B** = browser scenario.

| # | Case | Expected | Layer |
|---|---|---|---|
| T1 | Change with the correct current password | 200 + new token; old password 401 at login; new password 200 | U, V |
| T2 | Wrong current password | 403, nothing written; the 6th in 15 min → 429 | U |
| T3 | **Stolen token, no password** | Cannot change; throttled | U |
| T4 | **Other device's token after a change** | 401 `session_ended` on every route incl. `DELETE /api/me` and `POST /api/coach` | U, V |
| T5 | The changing device's new token | Works | U, V |
| T6 | **Expired token** (clock past `exp`) | 401 "Token has expired"; cannot change or recover-code-generate | U |
| T7 | Pre-release token without `tv` | Works until the first change, then 401 | U |
| T8 | Deleted account's token | 404 (unchanged) | U, V |
| T9 | Policy failures (short, long, same, equals username, common list if shipped) | 400 with code; **no slow hash spent** (timing assertion with a spy on the hasher) | U |
| T10 | Malformed bodies | 400 | U |
| T11 | Concurrent change + deletion | No 500; consistent either order | PG |
| T12 | Concurrent two changes | Serialised; one wins, the other sees the new state and answers 403 | PG |
| T13 | Logs for T1–T10 | No password, hash, token or code in any record | U |
| T14 | Export | `password_changed_at` present; no hash | U |
| T15 | Migration up/down/up on both dialects | Schema equals models | U, PG |
| T16 | Client: `session_ended` shows the sign-in screen with the reason; Settings change flow | As described | B |
| R1 | Generate codes (requires password) | 10 codes once; only HMACs stored; regenerate voids the old ones | U |
| R2 | Recover with a valid code | 200; code used; old password and tokens refused | U, V |
| R3 | Reuse a used code | 401 | U, V |
| R4 | Unknown user vs wrong code | Identical status and body; both do one HMAC lookup | U |
| R5 | Recovery cost | One HMAC + one indexed query per attempt; **no** slow hash before a code matches | U (spy) |
| R6 | Per-username failure limit | Recovery 429 for that name; **login still works** | U |
| R7 | Degraded rate-limit store | 503, never unthrottled | U |
| R8 | Account deletion | Code rows deleted; FK-classification test passes | U, PG |
| R9 | Pepper missing | Recovery and generation refuse (503); self-check reports it | U |
| R10 | Client recovery flow | Works end to end on a disposable server | B |

---

## 4. Owner decisions

1. Approve PW-1 (token version + change), then PW-2 (recovery codes), as
   separate releases.
2. Common-password list: source and licence, or none.
3. Apply the policy to registration as well?
4. A new secret, `RECOVERY_CODE_PEPPER`, must be created in Render by the
   owner. Never generated or seen by the assistant.
5. Email-based recovery (R2) stays out of scope unless the product decides to
   hold email addresses.
