# Password policy

**One policy for every new password.** Registration and password change both
call `_password_policy_problem(username, password)` in `app.py`. Existing
passwords are **never re-judged**: sign-in, deletion and the current-password
check accept whatever an account already has.

## Rules (checked in this order, on NFKC + case-folded text, with no hashing)

| Code | Rule | Why |
|---|---|---|
| `too_short` | Fewer than **15** characters (NFKC code points) | NIST SP 800-63B-4 sets 15 for a password used as the only factor, as StreakFit's is |
| `too_long` | More than 128 | Bounds the input. PBKDF2's cost doesn't depend on length (measured: ~109 ms at 16 and at 200,000 characters), so this is hygiene, not a DoS limit |
| `username_based` | Equal to the username, or the username (4+ characters) is **at least half** the password | Context-specific words. **Not** plain containment: "anna" inside "Savannah-lantern-meadow" isn't a weakness (review B3) |
| `service_name` | Nothing but "streakfit" or "rickie" once digits and symbols are ignored | Context-specific words |
| `repetitive` | One unit of 1–3 characters repeated | NIST's own example |
| `sequential` | A straight run of code points (`abcd…`, `9876…`) | NIST's own example |
| `common` | On the common/breached list (`docs/security/password-blocklist.md`) | NIST: a blocklist of commonly used, expected or compromised values |

There are no composition rules and no expiry. Messages are in
`_PASSWORD_POLICY_MESSAGES`.

- **Registration** answers `400 {"error": <message>, "code": <code>}`.
  `error` remains the human sentence that every existing client displays.
- **Password change** answers `400 {"error": "password_policy", "code", "message"}`.

## Unicode: one password whatever the keyboard, and nobody locked out

| Path | Rule |
|---|---|
| **New passwords** (registration, change) | Hashed in **NFKC** (`_password_hash_form`). An accented letter typed composed or decomposed, or a full-width form from some input methods, is one password. |
| **Checking a stored hash** (sign-in, deletion, current password) | `_password_matches` tries, in order: the NFKC form; the **raw string exactly as typed** (every hash written before normalisation is of the raw string); and for non-ASCII input only, NFC and NFD. Duplicates are skipped. |
| **One-time upgrade** | A sign-in that matched on anything but NFKC rewrites the hash in NFKC (`_upgrade_password_hash`). This happens under the row lock and **only if the hash is still the one just verified**, so a change committed in between is never overwritten. The token version doesn't move. If the upgrade fails, it's logged and ignored; the sign-in has already succeeded. |

**Guarantee:** anything that signed in before this change still signs in when
typed the same way. Tested in `test_every_existing_password_still_signs_in_exactly_as_typed_before`
with ASCII, accented and full-width legacy passwords.

**Fixed as a side effect:** an account stored in one composition (NFD) now
signs in from a keyboard that produces the other (NFC). That was review
finding A4, reproduced as a 401 before.

## Cost and denial of service

- **An ASCII password** costs one hash check, exactly as before (tested).
- **A non-ASCII guess** costs at most four.
- **An unknown username** costs exactly as many checks as a known one, so
  timing reveals nothing (tested).
- **Every caller is throttled:**
  - login: 5 failures/min and 30/hour per IP;
  - password change: 5 wrong current passwords per 15 min and 10/day per
    user;
  - deletion: 5/hour per user.
- **New-password policy failures never hash.**

## Owner decisions taken (2026-10-12)

- A 15-character minimum for new passwords.
- The same policy at registration.
- A locally held blocklist instead of an online breach API, so nothing about
  a password leaves the server.
