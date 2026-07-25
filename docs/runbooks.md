# StreakFit Operational Runbooks

Concise, command-level runbooks for common operational events. Commands assume the
repo root and the production `DATABASE_URL`/secrets in the environment. Steps that
depend on infrastructure this repo can't see are marked **[Render dashboard]** or
**[verify]** rather than invented.

Baseline facts:
- Deploy: git-linked to Render — `git push origin main` auto-deploys (~1 min).
- Start Command: `flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app`.
- Health: `GET https://streakfit.pro/health` → `200 {"status":"ok"}`.
- Single gunicorn worker (no `WEB_CONCURRENCY`); in-process caches + `memory://`
  rate limiting are therefore per-worker.

---

## Deployment
1. `git status` clean; run the suite locally (`pytest tests/`).
2. `git push origin main`. Render builds and runs the Start Command.
3. Watch **[Render dashboard]** for build/deploy success; the migration runs first.
4. Verify: `curl -s -o /dev/null -w '%{http_code}' https://streakfit.pro/health` → 200.
   For a frontend change, confirm the service-worker cache version bumped in
   `static/sw.js` and hard-refresh one device.

## Migration failure (deploy won't start)
- Symptom: deploy fails at `flask db upgrade`, or gunicorn refuses to boot with
  `SystemExit(1)` (DB not at Alembic head).
- The boot guard is intentional — **the app will not serve on a mismatched schema.**
1. Read the build log **[Render dashboard]** for the failing revision.
2. Reproduce locally against a **copy** of the DB: `flask db upgrade` and read the error.
3. Fix the migration; re-deploy. If a migration must be undone: `flask db downgrade -1`.
4. Never hand-edit the production schema to "match" — fix the chain so
   `tests/test_migrations.py` (from-empty parity) passes.

## Health-check failure (`/health` not 200)
1. `curl -i https://streakfit.pro/health`. 5xx/timeout → app down or DB unreachable.
2. Check **[Render dashboard]** logs for tracebacks and the DB connection status.
3. `/health` runs a `SELECT 1` — a failure there points at the database (see below).
4. If a bad deploy: **Rollback** (below).

## Anthropic (Rickie) outage
- Blast radius is contained: `/api/coach` returns `503 coach_unavailable`; the rest
  of the app is unaffected (Rickie fails closed, never fabricates).
1. Confirm scope: only coach requests failing? Check logs for coach exceptions.
2. Verify `ANTHROPIC_API_KEY` is set **[Render dashboard]** (missing key → 503 by design).
3. Check status.anthropic.com. No action needed beyond monitoring — the degraded
   state is safe. Do not disable the endpoint.

## Open-Meteo 429 / weather outage
- Symptom: Rickie says he "couldn't reach the weather"; logs show
  `weather lookup failed for '<city>': HTTPError: 429`.
- Cause: Open-Meteo's per-IP free limit (600/min, 10k/day) hit on Render's **shared**
  egress IP (see `docs/memory_pipeline.md`). Weather degrades gracefully — no
  hallucinated forecast.
1. Grep logs for the frequency: `event=weather_cache result=miss` and
   `weather lookup failed`. Rising misses + 429s = the shared IP is saturated.
2. The cache (geocode 30d / forecast 10m) already minimizes our calls — nothing to
   restart.
3. If persistent/frequent: escalate to a **keyed provider** (Open-Meteo API key with a
   per-account quota). Tracked as the documented escalation, not urgent.

## Database connection exhaustion
- Symptom: `SELECT 1` health failures, `QueuePool limit`/timeout errors in logs.
1. Check the pool config in `app.py` (`SQLALCHEMY_ENGINE_OPTIONS`) and the DB's
   max-connections **[verify Render Postgres plan]**.
2. Look for a leak — long-running/held sessions. The coach persist path is now one
   short transaction (WS1); admin verify runs in a background thread with its own
   `app_context`.
3. Short term: restart the service **[Render dashboard]** to drop stale connections.
   Longer term: raise pool size / DB plan, or add `pool_pre_ping`.

## Rollback
1. Identify the last-good commit: `git log --oneline`.
2. `git revert <bad_sha>` (preferred — keeps history) **or** reset a branch to the
   good SHA, then `git push origin main` to redeploy.
3. **Migration caveat:** if the bad deploy ran a forward migration, reverting code
   alone leaves the schema ahead. Run `flask db downgrade` to the matching revision
   **before/with** the code rollback, or the boot guard will refuse to start.
4. Verify `/health` → 200 and a normal login.

## Account deletion (a specific user)
Use the WS5 service (transactional, preserves shared team data). In a shell where the
prod `DATABASE_URL` is set:
```python
python -c "import app; \
 print(app.delete_user_account(<USER_ID>, dry_run=True))"      # preview
python -c "import app; \
 print(app.delete_user_account(<USER_ID>, dry_run=False))"     # execute
```
- Team owners are **blocked** (report shows `blocked: true`) — teams are shared;
  reassign ownership or handle the team first.
- Deletes private data (progress, coach memory); anonymizes authored team
  messages/moments (SET NULL); one transaction; idempotent.

## QA account cleanup (`qa_smoke_*`)
```bash
python scripts/cleanup_qa_smoke.py            # DRY RUN — review both groups
python scripts/cleanup_qa_smoke.py --execute  # delete the SAFE group only
```
- "SAFE" = no team owned; "REQUIRES MANUAL CLEANUP" = team owners (left untouched).
- Uses `delete_user_account` under the hood. Runs from repo root (no `PYTHONPATH`).

## Forgotten-conversation deletion (a user pressed "Forget")
- Handled by the app: `DELETE /api/coach/memory` (JWT-scoped to the caller) wipes
  their `coach_turn` + `coach_note`. No operator action normally needed.
- Manual, for one user: `python -c "import app; \
  app.app.app_context().push(); app._forget_coach_memory(<USER_ID>)"`.

## Investigating Coach Notes issues
- Logs: `event=coach_note_extract user_id=… goals/prefs/notes` (only when a fact was
  stored), `event=coach_memory_inject`.
- Inspect a user's notes:
  ```python
  python -c "import app; app.app.app_context().push(); \
   n=app.CoachNote.query.filter_by(user_id=<ID>).first(); \
   print(n.goals, n.preferences, n.notes) if n else print('none')"
  ```
- Extraction is deterministic regex on the user's own words (`_coach_note_extract`) —
  no model involvement. To clear bad notes for a user, use the Forget path above.

## Restore from backup
- **[verify Render Postgres]** — restore is a Render Postgres feature, not in this repo.
1. Take/confirm a fresh backup before any risky operation (Render Postgres → Backups).
2. To restore: use the Render dashboard point-in-time restore / backup restore flow
   **[Render dashboard]** — exact steps depend on the Postgres plan.
3. After restore, confirm the schema is at the Alembic head (`flask db current`) or
   the boot guard will block startup; run `flask db upgrade` if behind.
4. Verify `/health` → 200 and a normal login.
