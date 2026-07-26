# StreakFit Security Review

Focused application-security audit of `app.py` (overnight WS10). Overall the code
is defensively written: ORM-only queries, per-user scoping on nearly every route,
env-required secrets, generic error responses. **No critical or high-severity
exploitable issues were found.** IDOR, SQL injection, SSRF, CSRF, error leakage,
and secret handling are all handled correctly.

Severity legend: 🔴 critical · 🟠 high · 🟡 medium · 🔵 low · ⚪ info/positive.

## Findings & remediation

| # | Finding | Sev | Fixed? | Test |
|---|---|---|---|---|
| 1 | **No security headers** (HSTS / X-Frame-Options / X-Content-Type-Options / CSP). PWA could be framed/MIME-sniffed. | 🟡 | ✅ `after_request` adds `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `CSP: frame-ancestors 'none'`, HSTS. | `test_security_headers_present` |
| 2 | **Admin-secret compare not constant-time** (`secret != env_secret`) — timing oracle could recover `ADMIN_SECRET`. | 🟡 | ✅ `hmac.compare_digest`, still fails closed when unset. | `test_admin_route_*` (3) |
| 3 | **No request-body size limit** — multi-MB POST could exhaust the single worker. | 🟡 | ✅ `MAX_CONTENT_LENGTH = 256 KB`. | `test_oversized_request_body_rejected` |
| 4 | **Login timing side-channel** — `check_password_hash` skipped when user absent, so non-existent usernames return faster (enumeration despite ambiguous error). | 🔵 | ✅ Always hash against a fixed dummy when the user is absent. | `test_login_nonexistent_user_returns_401_not_500` |
| 5 | **Username enumeration via register** ("that username is taken"). | 🔵 | ⚠️ Accepted — inherent to unique usernames; mitigated by the 5/min register limit + monitoring. Not changed. | — |
| 6 | **Prompt injection via Coach Notes** — a user's `remember that <instruction>` is stored and injected into *their own* system prompt. | 🔵 | ⚠️ Low-impact by design: self-scoped, 140-char/first-clause cap, newlines already stripped by `_clean_fact`, sole tool is weather (hardcoded host). Block already labels notes "never recite." No code change. | — |
| 7 | **SSRF in weather tool** | ⚪ | Not vulnerable — outbound host is a hardcoded literal; user input only fills a query-string value; coords come from Open-Meteo's own response. (Optional: disable redirect-following as belt-and-suspenders.) | — |
| 8 | **AuthN/AuthZ & IDOR** | ⚪ | Well handled — every user/team route is `@jwt_required()` and scoped to `get_jwt_identity()`; team reads/writes verify membership; creator-only actions verify `created_by_user_id`; coach-memory delete strictly token-scoped. No IDOR found. | existing team/coach tests |
| 9 | **JWT config** | ⚪ | Good — secrets env-required (hard-fail if missing), 1h expiry, string identity. No refresh/denylist (acceptable at this scale). | — |
| 10 | **CSRF** | ⚪ | N/A — JWT in `Authorization` header, no cookie credential. | — |
| 11 | **Password handling** | ⚪ | Good — PBKDF2-SHA256, 8–128 length bounds. (Optional: argon2/scrypt later.) | — |
| 12 | **SQL construction** | ⚪ | Safe — ORM/`db.select` only; the sole raw SQL is a static `SELECT 1` health probe. | — |
| 13 | **Error leakage** | ⚪ | Safe — generic JSON to clients; traces to server logs only. | — |
| 14 | **Logging** | ⚪ | Safe — structured `event=… user_id=…`; no secrets/tokens/PII. | — |
| 15 | **Rate limiting / brute force** | 🔵 | Partial — login 10/min, register 5/min, coach 10/day+3/min. **QUANTIFIED 2026-07-26 — severity raised.** The missing `ProxyFix` is not cosmetic: the limiter keys on `remote_addr`, measured in production as `10.26.173.131` (Render-internal), so limits are enforced per internal address rather than per client. One keep-alive connection gets exactly 5/min on `/api/register`; spread across connections, 30 of 50 and 37 of 60 requests are allowed — **~6–7× every configured limit**. `/api/coach` has no application-level cap behind flask-limiter, making its `3/min` + `10/day` effectively ~20/min + ~70/day on a **paid** API. Determination and evidence: [operations/rate-limiting-client-ip.md](operations/rate-limiting-client-ip.md) — trust `X-Forwarded-For` second-from-right (`ProxyFix(x_for=2)`); note Render's own "first IP" guidance would be **spoofable**. `memory://` (per-worker, resets on deploy) is a separate, smaller issue and must NOT be fixed first. | — |

## Unresolved / accepted risks
- **#5, #6** — accepted low-severity trade-offs (documented above), no change.
- **#15** — rate-limit storage is process-local; multi-worker durability needs a shared backend. Addressed as a documented recommendation in the WS7 notes rather than adding Redis.
- **Optional hardening not done** (low priority): disable redirect-following in the weather HTTP client; argon2 password hashing; a full script/style CSP (needs testing against the PWA's inline JS — deferred to avoid breaking the frontend).

## What changed in code
`app.py`: `import hmac`; `MAX_CONTENT_LENGTH`; `_DUMMY_PW_HASH`; `after_request`
security-headers hook; constant-time admin compare; login timing equalization.
Tests: `tests/test_security.py` (6). No user-facing behavior change; all additions
are defense-in-depth.
