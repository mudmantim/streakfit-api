# Security hardening (S3, S4, D1) — what ships, how to deploy, how to roll back

**Branch:** `fix/security-hardening`, built on production `6b6e1cd`.

**Not released.** Any release needs owner approval. Findings are from the
2026-10-12 audit
(`e2e-campaign/evidence/d6_d50_d59/STEP2_READINESS_2026-10-12.md`).

## What changes

| # | Change | Who notices |
|---|---|---|
| S4 | `POST /api/register`, `POST /api/login`, `DELETE /api/me` (and `PATCH /api/me`, `PUT /api/daily/effort`) answer a malformed body — not JSON, a JSON array, a number or list where text belongs — with **400** instead of a 500. | Nobody using the app; only broken or hostile clients |
| S3 | The seven `/api/admin/reports…` and `/api/admin/appeals…` routes are rate limited. Wrong `X-Admin-Secret` answers (403 on a request that *sent* a secret) are charged to **one bucket shared by all seven**, 10/minute and 50/hour per IP; the 11th wrong guess in a minute gets 429. Correct requests are never charged to that bucket. Separate ceilings: 120/min per read route, 30/min per write route. Authorization is unchanged. | An operator who types the secret wrong ten times in a minute waits a minute. Someone guessing it is stopped. Requests with **no** secret (the verification suite checks that refusal on every route) are refused but not charged; charging them made a local `verify_all` run lock its own IP out (found and fixed before commit). |
| D1 | Effort (`PUT /api/daily/effort`) and skill (`PATCH /api/me` with `skill_level`) accept the page's `mission_date`. A day that has ended → **409 `day_changed`**, nothing written, and the page moves to the new day. Today's date → as before. A future date → 400. **No `mission_date` → exactly as before** (old pages keep working), logged as `event=undated_day_write route=effort\|skill user_id=…`. | A tab left open overnight: an effort or skill tap now shows "A new day has started" instead of silently reshaping today's five |

- The client (`static/app.js`) now sends `mission_date` on both writes and
  handles `day_changed`. Its dead 409 branch on effort is gone.
- `static/sw.js` cache: `v1006` → `v1007`.
- Verification suite: **v14 → v15**.
  - `auth.malformed_login_is_400`
  - `auth.delete_malformed_is_400`
  - `mission.stale_effort_and_skill_are_refused`

No migration, no schema change, no data change.

## Compatibility

- **Pages open across the deploy** (old JavaScript) send no `mission_date` on
  effort or skill. They are served exactly as today, and that includes
  today's stale-tab behaviour, which only a reload fixes.
  - After a week of `event=undated_day_write` going quiet, the owner may
    decide to require the field, as completions already do.
- **API clients** sending well-formed JSON see no difference. Only malformed
  requests change: 500 becomes 400.
- **`verify_all` v14 against this build:** passes; its checks are a subset.
  **v15 against production `6b6e1cd`:** the three new checks are expected to
  fail there (not run — production was not touched; the same contracts fail
  in pytest and in a browser against `6b6e1cd`). Run v15 only after deploying
  this.

## Deploy

As the Step 1 runbook (`deploy-step1-daily-mission.md`, "Deploy sequence"):

1. Exact 40-character SHA, manual deploy, Auto-Deploy Off.
2. The Pre-Deploy `flask db upgrade` is a no-op: head stays `d4f1b6e2a9c3`.
3. A fresh backup is optional: there is no schema change and no data change.
   Owner's call.

**After deploy, read-only checks:**

- build identity (new SHA, `d4f1b6e2a9c3`, `atHead`), `/health`, one worker;
- no Traceback or 5xx, with a positive control.

**Owner-approved only:** `verify_all` v15 against production with an
explicit base URL.

## Roll back

**Redeploy `6b6e1cd`.** No downgrade, because there is no migration.

- Pages loaded from this build keep working against the old build. It ignores
  the extra `mission_date` field, so effort and skill revert to the old
  behaviour. Their `day_changed` handling simply never triggers.
- The in-memory rate-limit counters reset on restart either way.
