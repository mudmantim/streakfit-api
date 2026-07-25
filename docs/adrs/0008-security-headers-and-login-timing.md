# ADR-0008: Security headers & login-timing hardening
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
StreakFit is a public PWA plus JSON API. A security review of `app.py` (see
[`../security_review.md`](../security_review.md)) found no critical or high
issues — IDOR, SQL injection, SSRF, CSRF, and error leakage are all handled —
but flagged several cheap medium/low hardening gaps: no security response headers
(the PWA could be framed or MIME-sniffed), no request-body size limit (a multi-MB
POST could exhaust the single worker), a non-constant-time admin-secret compare
(a timing oracle on `ADMIN_SECRET`), and a login timing side-channel (skipping
the password hash for unknown usernames made them measurably faster to reject,
enabling enumeration despite the deliberately ambiguous error message).

## Decision
Add defense-in-depth with no user-facing behavior change:

- **Security headers** via an `after_request` hook (`_security_headers`), all set
  with `setdefault` so an explicit per-response value is never clobbered:
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy:
  strict-origin-when-cross-origin`, `Content-Security-Policy: frame-ancestors
  'none'`, and HSTS `max-age=31536000; includeSubDomains` (honored by browsers
  only over HTTPS, which Render serves).
- **Request-body cap** `MAX_CONTENT_LENGTH = 256 KB` — every real body is tiny
  (the coach message caps at 500 chars), so oversized bodies are rejected before
  parsing.
- **Constant-time admin gate.** `_require_admin_secret` compares the
  `X-Admin-Secret` header to `ADMIN_SECRET` with `hmac.compare_digest` on
  UTF-8-encoded bytes, and still fails closed when the env secret is unset/empty.
  (Bytes, not str, because `compare_digest` raises `TypeError` on non-ASCII str —
  which would 500 instead of a clean 403.)
- **Constant-time login.** `login()` always runs `check_password_hash`, against a
  fixed `_DUMMY_PW_HASH` when the username doesn't exist, so response time no
  longer reveals whether a username is valid; the 401 message stays ambiguous.

## Alternatives considered
- **Do nothing** (rely on no-high-severity-findings). Why it lost: these are
  low-cost, low-risk, standard hardening; leaving free defense-in-depth on the
  table on a public app is the wrong trade.
- **A full script/style CSP** (not just `frame-ancestors`). Why it lost *for
  now*: the PWA has inline JS; a strict `script-src`/`style-src` CSP needs
  testing against the frontend to avoid breaking it, so it's deferred rather than
  shipped blind. `frame-ancestors 'none'` is the safe subset shipped today.
- **`!=` admin compare / skip-hash login** (the pre-hardening code). Why it lost:
  both are timing oracles — byte-by-byte secret comparison and a fast-path for
  absent users leak information an attacker can measure.
- **Argon2/scrypt password hashing, disabling redirect-following in the weather
  client.** Why deferred: optional, lower-priority hardening noted in the review;
  PBKDF2-SHA256 with length bounds is acceptable at this scale, and the weather
  host is a hardcoded literal (not SSRF-exploitable).

## Why the current solution won
Each change is a small, well-understood, standard mitigation with no behavior
change for legitimate clients, closing a concrete finding from the review. Using
`setdefault` for headers keeps the hook from overriding intentional per-route
values; encoding to bytes before `compare_digest` fails *closed* (clean 403)
instead of 500; and equalizing login timing removes username enumeration while
preserving the ambiguous error. Together they are cheap insurance appropriate to
a public app, deferring only the changes that need frontend testing.

## Consequences & future tradeoffs
- **Makes easy:** a baseline security posture (anti-framing, anti-sniffing,
  HSTS), DoS resistance to oversized bodies, and no timing oracles on the admin
  secret or usernames — all covered by `tests/test_security.py`.
- **Makes hard:** the 256 KB cap is global, so any future large-body endpoint
  (e.g. an upload) would need a per-route exception. The shipped CSP is
  frame-only — it does not constrain script/style sources yet, so it is not XSS
  mitigation. Login always paying a hash cost is a deliberate (tiny) latency add.
- **When we'd revisit:** ship a full script/style CSP once tested against the
  PWA's inline JS; consider argon2 and disabling weather-client redirects as the
  next optional layer; revisit the body cap if a large-payload endpoint appears.

## Code references
- `app.py:69` — `MAX_CONTENT_LENGTH = 256 * 1024`.
- `app.py:73` — `_DUMMY_PW_HASH` (fixed timing-equalizer hash).
- `app.py:76-85` — `_security_headers` `after_request` hook (all via `setdefault`).
- `app.py:1418-1429` — `_require_admin_secret`: `hmac.compare_digest` on bytes,
  fails closed when `ADMIN_SECRET` unset.
- `app.py:1919-1926` — `login()` always hashes (dummy hash for absent user),
  ambiguous 401.
- Tests: `tests/test_security.py`. See
  [`../security_review.md`](../security_review.md) for the full audit and
  [`../architecture/authentication.md`](../architecture/authentication.md).
