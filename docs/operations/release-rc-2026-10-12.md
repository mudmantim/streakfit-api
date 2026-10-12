# Release candidate `rc/2026-10-12` — review, deployment plan, rollback plan

**Status: candidate, not released.** Production is `6b6e1cd` (Step 1,
Alembic head `d4f1b6e2a9c3`).

- **The exact candidate SHA** is the tip of `rc/2026-10-12`. It is recorded,
  with every verification result, in the release record
  `e2e-campaign/evidence/d6_d50_d59/RC_2026-10-12.md`. A file can't contain
  its own commit's SHA.
- **Nothing here is executed by the assistant.** Every production step is the
  owner's.

## What the candidate contains

It descends from `6b6e1cd`. Integrated in this order, with no conflicts:

| Source | Commits | Kind |
|---|---|---|
| `housekeeping/step1-post-release` + `review/housekeeping-corrections` | `42ca91e`, `0a20367` (fast-forward) | Docs: Step 1 runbook as released, rollback lock timeout, backup deadline |
| `proposal/ci-postgres` | `39d07cf`, `3583580` | CI: PostgreSQL job (pinned 17.10 digest, no-skip guard), PostgreSQL migration test, read-only token, actions pinned to SHAs |
| `fix/security-hardening` | `57b7469` | **The only application change** (below) |
| `design/password-and-child-safety` | `90c74ae` | Docs (design only) |
| `design/step2-local-day` | `f3b4875` | Docs (design only) |
| this release commit | — | Docs: this plan, `child-safety/phased-plan.md`, `architecture/password-implementation-checklist.md` |

**Application diff against `6b6e1cd`:** `app.py`, `static/app.js`,
`static/sw.js` and `scripts/verification/*` are byte-identical to `57b7469`'s
diff. No migration. `requirements.txt` unchanged.

## Release-engineering review

| Area | Finding |
|---|---|
| **Behaviour changes** | Exactly three, all from `57b7469`. (1) Malformed bodies get 400 instead of 500 on register, login, delete, profile and effort. (2) Throttles on the 7 moderation routes; failures are charged only when a secret was sent. (3) Effort and skill with an ended `mission_date` → 409, nothing written; a future or invalid date → 400; **no `mission_date` → unchanged**. Nothing else in `app.py` moved. |
| **Migrations** | None. Pre-Deploy `flask db upgrade` is a no-op. Head stays `d4f1b6e2a9c3`. |
| **DB-head enforcement** | Unchanged. `STREAKFIT_ENFORCE_DB_HEAD=1` passes, because the code's head equals the database's. |
| **Service worker** | `streakfit-v1006` → `v1007`. The new worker's activate step deletes every other cache. App-shell assets are content-versioned (D49, `test_sw_asset_versioning.py`), so a reload always gets the new `app.js`. A page left open runs the old JS until reload; that is safe (next row). |
| **Old pages / existing users** | Old JS sends no `mission_date` on effort or skill and is served as before (logged `event=undated_day_write`). Completions and Brain Boost already required the day; unchanged. Well-formed requests from any client behave identically. |
| **Authentication** | No token change. JWT settings, claims and expiry unchanged. Login responses unchanged except malformed bodies (400). A deleted account's old token is still 404. |
| **Rate limits** | Three new limit definitions, stored wherever `RATELIMIT_STORAGE_URI` points. Production configuration isn't visible to the assistant; the D7 release record says shared Redis. Check, don't assume. The shared failure bucket is keyed by client IP after `ProxyFix(x_for=2)`, the same as login. Operator impact: 10 wrong secrets per minute (50/hour) from one IP → 429 for the window. `verify_all` sends no secret, so it is never charged. |
| **Privacy and retention** | Unchanged. One new log line (`event=undated_day_write route=… user_id=…`) carries a user ID and route, never request values (tested). Retention sweeps untouched. |
| **CI configuration** | `permissions: contents: read`. Actions pinned to commit SHAs (v4/v5 as of 2026-10-12). The service-container password is a throwaway, not a secret. No repository secrets used. The PostgreSQL job fails if any test is skipped. **Not yet run on GitHub**: the first push runs it. |
| **Rollback safety** | No schema change, so rollback is a plain redeploy of `6b6e1cd`. New-build pages keep working against the old build, which ignores the extra `mission_date` field. |

## Deployment plan (owner-executed; not executed here)

**Preconditions**, each re-checked right before deploying:

1. The owner has reviewed the candidate and the release record.
2. **Make `main` equal to the candidate without a new commit:**
   `git push origin <RC_SHA>:main`.
   - This is a fast-forward, because the candidate descends from
     `origin/main` = `6b6e1cd`. Render then lists the reviewed SHA itself.
   - If `main` has moved, stop: the candidate must be rebuilt on the new
     `main` and re-verified.
3. CI on that push is green in **both** jobs:
   - `test`: lint, mypy, build gate, the SQLite suite;
   - `postgres`: 42 tests, no skips.

   This is the first real run of the `postgres` job; if it fails for an
   environmental reason, fix it before deploying.
4. Production `/api/build-identity` still shows `6b6e1cd`, `d4f1b6e2a9c3`,
   `atHead: true`. Auto-Deploy Off. One instance.
5. Outside 23:30–00:30 UTC and Render maintenance.
6. **Backup:** not required, because there is no schema or data change. If the
   owner wants one, take it with the custody scripts; its 14-day deadline is
   printed.

**Deploy:**

1. Render → `streakfit-api` → Manual Deploy → Deploy a specific commit →
   paste the full 40-character RC SHA → select it → Deploy Commit. Record the
   `dep-…` ID.
2. Watch the log:
   - checkout of the exact SHA;
   - Pre-Deploy `flask db upgrade` with **no** "Running upgrade" line;
   - `Database migration check passed (at head d4f1b6e2a9c3)`;
   - one `Booting worker`;
   - "Deploy succeeded".

**After deploy (read-only):**

1. `/api/build-identity`: the RC SHA, `d4f1b6e2a9c3`, `atHead: true`,
   `appliedCount` 28.
2. `/health`: 200.
3. Logs since go-live: no `Traceback` or 5xx, with a positive control (a
   search for 200 finds requests).
4. Over the next days, watch `event=undated_day_write`. It should fall to
   zero as old tabs reload.

**Owner-approved only:** `verify_all` v15 against production with an explicit
base URL. It creates throwaway `qa_smoke_*` accounts. Locally it scored
214/214 on this candidate. It never sends an admin secret, so it cannot trip
the new admin throttle.

## Rollback plan (owner-executed; not executed here)

**Trigger:** any 5xx regression, auth failure, or mission write misbehaviour
attributable to the release.

1. Render → Manual Deploy → Deploy a specific commit →
   `6b6e1cd02a6a53ffc10ab1896187787ff1002163`.
   - **No downgrade:** the head is unchanged, so the old build's head check
     passes.
2. Check:
   - `/api/build-identity` shows `6b6e1cd…` and `d4f1b6e2a9c3`, `atHead: true`;
   - `/health` 200;
   - one worker;
   - no Traceback.
3. Effects:
   - the three behaviour changes revert;
   - pages from the candidate keep working (the extra field is ignored);
   - the service worker cache name goes back to v1006 on next load, which is
     harmless.
4. `main` keeps the candidate commits. Revert or roll forward with a fix,
   through review as usual.

Deeper rollback, to before Step 1, is the separate procedure in
`deploy-step1-daily-mission.md`, with the downgrade first and
`PGOPTIONS='-c lock_timeout=5s'`.
