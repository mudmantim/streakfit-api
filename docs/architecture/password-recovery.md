# Password recovery — design options

**Status: design only. Nothing here is implemented. No email provider is added.**
Written 2026-10-12. It depends on the token-version mechanism in
`password-change.md` §3: every recovery ends by incrementing `token_version`,
which signs out every device.

## The constraint that shapes everything

StreakFit stores **no email address, no phone number and no real name** (see
the `User` model comment and `docs/operations/minors-and-consent.md`). That
is its strongest privacy property, and the product is used by families.

"Email me a reset link" is therefore not a feature that can simply be added.
It means **starting to collect contact details**. For anyone under 13, an
email address is "online contact information", which counts as personal
information under COPPA and brings in the consent questions in
`docs/child-safety/`.

So the options are ordered by how much they change what the product collects.

---

## Option R1 — recovery codes (recommended first; no provider, no new personal data)

**How it works:** in Settings, a person generates **10 single-use recovery
codes**, shown once, to print or save.

- Each code is 12 characters from a 32-symbol unambiguous alphabet, about 60
  bits.
- The server stores only a slow hash of each code, the same scheme as
  passwords, in a table `recovery_code(user_id FK, code_hash, created_at, used_at)`.
- Generating a new set invalidates the old set.

**Recovery:** `POST /api/recover`, with body
`{username, recovery_code, new_password}`.

- The username is looked up, and every unused code hash is checked. A dummy
  hash comparison runs when the user is unknown, as login does, so timing does
  not reveal which accounts exist.
- On success, in one transaction:
  - mark the code used;
  - set the new password (the policy from `password-change.md` §2);
  - `token_version += 1`;
  - set `password_changed_at`.
  - Returns a fresh token.
- **The same 400/401 answer whether the username or the code was wrong.**

**Abuse controls:**

| Control | Setting |
|---|---|
| Per IP | 5/hour on failures (`deduct_when` 401), 20/day overall |
| Per username | 10 failures/day. It locks only *recovery* for that name, never login, so it cannot be used to lock a person out of their account |
| Degraded rate-limit store | `@sensitive_when_degraded("refuse")`. Recovery waits a few minutes rather than run unthrottled |
| Code space | 60 bits × 10 codes against ~10 guesses/day per name is negligible |

**Audit:** log `event=recovery_used user_id=… codes_left=N` and
`event=recovery_refused` (no username in the refusal line, because a typed
username can be an email address). Never a code, a password or a hash.

**Account deletion:** the `recovery_code` rows are added to
`_USER_PRIVATE_DELETES`. The FK-classification test enforces this.

**Weaknesses:**

- People lose codes.
- Existing accounts have none until they generate them, so they need a Settings
  prompt ("Make recovery codes — you can't reset a forgotten password without
  them").
- An account with no codes stays unrecoverable. That is no worse than today.

**Children:** a guardian could hold a child's codes. This collects nothing new.

## Option R2 — email reset (needs owner approval, legal review and a provider decision)

Only if the owner decides StreakFit may hold an email address for this
purpose.

- **Collection:** an *optional* `recovery_email`, never required, never shown
  to anyone, never used for anything but recovery.
  - Verified by double opt-in: a link sent to it must be clicked before it can
    be used.
  - Stored with `verified_at`.
  - Included in the data export; deleted with the account.
  - Not offered to `child` or unknown-age accounts until the child-safety
    policy (`docs/child-safety/policy-options.md`) says how.
- **Reset token:**
  - 32 random bytes; stored hashed; single use; expires in 30 minutes;
  - bound to the user and the current `token_version`, so any password change
    voids outstanding links;
  - delivered in the URL **fragment** (`/reset#t=…`), so it never appears in
    server logs, proxies or the `Referer` header;
  - redeemed by `POST /api/recover/email {token, new_password}`.
- **Requesting a reset:** `POST /api/recover/request {username}` always
  answers the same 202, whether or not a verified email exists.
  - Registration already reveals whether a username is taken, so this does not
    remove enumeration. It just doesn't add a second channel.
- **Abuse controls:**
  - per IP 3/hour;
  - per account 3/hour and 5/day;
  - a global daily cap on recovery emails. When it trips, requests are refused
    and an operator notice is raised: protection against using the app to
    spam an address.
  - The email body contains no user-supplied text: no username, no display
    name.
  - After a successful reset, a notice goes to the same address: "your
    password was changed; if this wasn't you…".
- **Provider:** the existing `ResendChannel` is deliberately built so that
  **the recipient is configuration, never data**. It can only email the one
  operator address. Recovery would need a *new* channel class that sends to a
  user-supplied, verified address. That reverses a safety property on purpose,
  so it needs its own review, idempotency keys and bounce handling.
- **Legal and privacy review:**
  - the privacy notice and data export text;
  - COPPA for under-13s (email is personal information; verifiable parental
    consent may be required before collecting it);
  - state teen laws;
  - the provider's data-processing terms.

## Option R3 — operator-assisted reset (not recommended)

The product holds nothing an operator could check identity against: no email,
no phone, no payment name for free accounts. Any manual reset would accept
"I am alice" on trust. That is a social-engineering path to take over a
child's account.

The one case worth revisiting is a child account with a verified guardian
link (`policy-options.md`, option B). There, a signed-in guardian could
re-issue the child's password, under the same lock and token-version rules.

## Tests required (R1, the recommended first step)

| # | Case | Expected |
|---|---|---|
| C1 | Generate codes | 10 shown once; only hashes stored; regenerating voids the old set |
| C2 | Recover with a valid code | 200 and a new token; old password and old tokens refused; code marked used |
| C3 | Reuse a used code | 401 |
| C4 | Unknown username vs wrong code | Identical status, body and (within tolerance) timing |
| C5 | Throttles: IP failures, per-name failures | 429. Login for that name still works |
| C6 | Degraded rate-limit store | 503, never unthrottled |
| C7 | Logs | No code, password, hash or token. No username in refusal lines |
| C8 | Account deletion | Code rows gone; FK-classification test passes |
| C9 | Policy on the new password | As `password-change.md` §2 |
| C10 | Migration chain on SQLite + PostgreSQL | Schema equals models |

## Owner decisions

1. Build R1 (recovery codes) as the first recovery path? **Recommended.**
2. Is StreakFit willing to hold an email address at all (R2)? That is a
   product-identity decision first and an engineering task second.
3. If R2 is chosen: provider (Resend or other), sender domain, legal review,
   and the under-13 rule.
