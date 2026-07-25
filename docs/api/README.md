# StreakFit Backend API

Human-facing guide to the StreakFit HTTP API. The machine-readable contract lives in
[`openapi.yaml`](./openapi.yaml) (OpenAPI 3.1) and covers every endpoint with request/response
schemas and examples. For how these routes fit the wider system, see
[`../architecture/README.md`](../architecture/README.md).

The entire backend is a single Flask app (`app.py`). Base URLs:

| Environment | Base URL |
|-------------|----------|
| Production  | `https://streakfit.pro` |
| Local dev   | `http://localhost:5000` |

---

## Authentication

Auth is a two-step, token-based flow:

1. **Register** — `POST /api/register` with `{"username", "password"}` (username 2–80 chars,
   password 8–128 chars). Creates the account; does **not** return a token.
2. **Log in** — `POST /api/login` with the same credentials. Returns
   `{"access_token": "<JWT>"}`.
3. **Call protected routes** — send the token on every request:
   ```
   Authorization: Bearer <access_token>
   ```

Tokens are **JWTs that expire 1 hour after issue**. When a token expires, re-run login to
get a fresh one. The token's identity is the user's integer ID; protected routes act only
on that user's own data.

### JWT failure responses

All three are JSON `{"error": ...}`:

| Situation | Status | Body |
|-----------|--------|------|
| Missing / malformed `Authorization` header | `401` | `{"error":"Missing or invalid token"}` |
| Invalid token (bad signature, undecodable) | `422` | `{"error":"Invalid token"}` |
| Expired token | `401` | `{"error":"Token has expired"}` |

### Which routes are public

No token required: `GET /`, `GET /sw.js`, `GET /health`, `GET /admin`,
`GET /api/demo/daily`, `POST /api/events`, `POST /api/register`, `POST /api/login`.
Everything else under `/api/*` requires a Bearer token — except `/api/admin/*`, which
uses the admin-secret scheme below instead.

---

## Admin-secret scheme

The `/api/admin/*` routes are **not** JWT-protected. They are gated by a shared secret in a
header:

```
X-Admin-Secret: <ADMIN_SECRET>
```

The value is compared (constant-time) against the `ADMIN_SECRET` environment variable, and
**fails closed** when that variable is unset or empty. A missing or wrong secret returns a
`403` — see the error-model exception below. The `GET /admin` HTML page itself is public;
only the `/api/admin/*` data endpoints it calls require the secret.

---

## Error model

The standard error body is JSON:

```json
{ "error": "<message-or-code>" }
```

Some handlers return human-readable sentences (e.g. `"Username needs to be 2–80 characters."`)
and some return machine codes (e.g. `"message_too_long"`, `"coach_unavailable"`). The
framework-level handlers also use this shape: `400 → "Bad request"`, `404 → "Not found"`,
`429 → "Too many requests. Please try again later."`, `500 → "Internal server error"`.

### Two exceptions that are **not** JSON

1. **`413 Payload Too Large`** — any request body larger than **256 KB**
   (`MAX_CONTENT_LENGTH`) is rejected by Werkzeug before the handler runs, returning Flask's
   **default HTML** 413 page. Applies to every route that accepts a body.
2. **`403` on `/api/admin/*`** — a missing/incorrect `X-Admin-Secret` triggers `abort(403)`,
   which returns Flask's **default HTML** 403 page, not the `{"error": ...}` shape.

> Note: the admin **data** failure `GET /api/admin/stats → 503 {"error":"stats_unavailable"}`
> *does* use the JSON shape — only the admin **auth** 403 is the HTML exception.

---

## Rate limits

Enforced per client IP by Flask-Limiter. Exceeding a limit returns `429` with
`{"error":"Too many requests. Please try again later."}`.

| Route | Method | Limit |
|-------|--------|-------|
| `/api/register` | POST | 5 / minute |
| `/api/login` | POST | 10 / minute |
| `/api/events` | POST | 30 / minute |
| `/api/coach` | POST | 10 / day **and** 3 / minute |
| `/api/teams` | POST | 10 / minute |
| `/api/teams/{id}/join` | POST | 10 / minute |
| `/api/teams/{id}/messages` | POST | 30 / minute |
| `/api/admin/stats` | GET | 120 / minute |
| `/api/admin/verify/status` | GET | 120 / minute |
| `/api/admin/project-status` | GET | 60 / minute |
| `/api/admin/system-health` | GET | 60 / minute |
| `/api/admin/verify/history` | GET | 60 / minute |
| `/api/admin/verify` | POST | 6 / minute |

Routes not listed here have no explicit per-route limit (the global default limit list is empty).

---

## Pagination & ordering

There is no offset/cursor pagination; list endpoints return fixed, ordered slices:

| Endpoint | Ordering | Limit |
|----------|----------|-------|
| `GET /api/teams/{id}/messages` | `created_at` **ascending** (oldest first) | all |
| `GET /api/teams/{id}/moments` | `occurred_at` **descending** (newest first) | all |
| `GET /api/memory-book` → `timeline` | `created_at` desc (newest first) | **last 30** `ProgressEvent`s |
| `GET /api/admin/verify/history` → `runs` | `id` descending (newest first) | last 20 runs |
| `GET /api/admin/stats` → `recent_users` | `id` descending | last 50 users |

`GET /api/challenges` and `GET /api/teams` return the caller's full set, unordered/unlimited.

---

## Conventions

- **Timestamps are UTC ISO-8601.** Most model timestamps are naive-UTC `isoformat()` with no
  suffix (e.g. `2026-07-23T12:00:00`). Some **admin** payloads deliberately append a literal
  `Z` (`generated_at`, `last_deployment_at`, and the verification `started_at`/`finished_at`) —
  noted per field in `openapi.yaml`. Date-only fields (challenge `last_check_in`, daily `date`)
  are `YYYY-MM-DD`.
- **IDs are integers** — user, team, challenge, and verification-run IDs. Path params
  `{team_id}`, `{challenge_id}`, `{member_user_id}` are ints; `{exercise_key}` and
  `{code}` are strings.
- **Request/response bodies are JSON** (`Content-Type: application/json`). Successful writes
  with no payload return `204` (e.g. `POST /api/events`).
- **Scoping:** every JWT-protected route operates only on the token owner's data.
  Team read routes require membership (`403 Forbidden` otherwise); `remove-member` and
  `rotate-invite` require being the team **creator**.
- **Idempotency:** completing an already-completed exercise, answering an already-answered
  Brain Boost, and checking in to a challenge twice in one day are all safe no-op repeats that
  return current state without duplicate awards.

---

## Notable side effects

Beyond ordinary row writes, a few routes reach further:

- **`POST /api/coach`** calls the **Anthropic** Messages API (`claude-sonnet-5`) and may call
  **Open-Meteo** (geocode + forecast) via Rickie's `get_weather` tool. It persists
  conversation memory (`CoachTurn`, rolling 10-turn window) and may update a `CoachNote`.
  Requires `ANTHROPIC_API_KEY`, else `503 {"error":"coach_unavailable"}`.
- **`POST /api/daily/{exercise_key}/complete`** can fan out on a completed mission: increments
  each of the user's teams' campfire totals and writes team moments and Rickie chat messages.
- **`POST /api/admin/verify`** spawns a background thread that runs the full verification suite
  in-process and writes a `VerificationRun` row.

See `openapi.yaml` for the exact per-operation side-effect notes.
