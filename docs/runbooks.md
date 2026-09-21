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

## Rate-limit backend outage (once shared storage is provisioned)
- **The app stays up.** Flask-Limiter falls back to per-worker in-memory
  counters, so throttled routes keep answering instead of returning 500.
  Measured; see [operations/rate-limit-backend-outage.md](operations/rate-limit-backend-outage.md).
1. `/api/verification/self` → `ratelimit.shared_storage` **FAIL**, observed
   `DEGRADED — backend configured but could not record a count`. That is
   the outage,
   reported; the endpoint itself keeps working during it.
2. Within ~15s the per-route degraded policies engage: invite-code lookup
   refuses with **503**, login drops to a tighter **per-process** cap.
3. **Recovery is automatic** — no redeploy. The check returns to PASS on its
   own once the backend answers.
4. Only escalate if it stays degraded: while degraded the limits are
   per-worker, so the effective allowance is multiplied by the worker count.

## Notification channel not delivering
1. `python scripts/notification_preflight.py` **[in a shell with the app's
   environment]** — names which variable is missing or malformed, and never
   prints a value. Add `--live` to confirm Resend accepts the key and the
   sending domain is verified. It sends no email.
2. Half-configured reads as not configured by design: the app refuses to build
   the channel and reports every notice undelivered rather than pretending.
3. `/api/verification/self` → `moderation.delivery_configured` carries the
   reason, by variable name.

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
   max-connections **[verify the Neon plan's connection limit]** — this is
   Neon, not Render Postgres, and pooled connections have a different ceiling
   from direct ones.
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

> **CORRECTED 2026-09-21. This section used to describe Render Postgres
> backups. Render does not host this database.** A read-only pass over the
> Render dashboard found exactly one managed Postgres instance in the account
> — `porchlight-db`, Oregon — which is PorchLight's, and `streakfit-api`'s
> environment page carries no linked-database section at all. Production
> reaches **Neon**, an external provider, and Neon's recovery model is not
> Render's. Following the old steps would have meant looking for a backup
> that was never being taken.

**There is no automatic dump export.** Neon does not produce backup files on a
schedule. If nobody has run `pg_dump`, there is no file to restore from — only
the history window, which is not the same thing (see below).

### The two mechanisms, which are not interchangeable

| | **Point-in-time branch** | **`pg_dump` file** |
|---|---|---|
| Lives | inside the same Neon project | wherever you put it |
| Time limit | only within the **history window** | none |
| Survives losing the project or the account | **no** | **yes** |
| Speed | instant (copy-on-write) | minutes |
| Use it as | a fast rehearsal target | **the actual recovery artifact** |

A branch is a cheap clone that shares the project's fate. If what you are
recovering *from* is damage to the project, the branch is gone with it.

The **history window** is per plan and must be read, never assumed: **Free is
6 hours** and cannot be raised; Launch is 1 day (up to 7); Scale is 1 day (up
to 30). Neon console → project → **Settings → Postgres → History window**. On
Free, a branch taken before an evening migration is worthless by morning.

### Restoring

0. **Identify the project first.** Match the hostname in Render's
   `DATABASE_URL` to a Neon endpoint ID before touching anything — there is
   more than one Neon project named `streakfit`. See
   `docs/operations/deploy-runbook.md` §3.1.
1. **Within the history window**, and for a fast recovery: restore the branch
   to a timestamp in the Neon console. Neon keeps a backup branch of the
   pre-restore state automatically, so the restore itself is reversible.
2. **Outside the window**, or if the project itself is the problem: create a
   fresh Neon branch or project and `pg_restore` the most recent dump into it,
   then repoint `DATABASE_URL`.
   ```
   pg_restore --no-owner --no-acl -d "$TARGET_URL" streakfit-<stamp>.dump
   ```
   Use the **direct** (non-`-pooler`) hostname — see "Pooled versus direct".
3. After any restore, confirm the schema is at the Alembic head
   (`flask db current`) or the boot guard blocks startup; run `flask db
   upgrade` if behind.
4. Verify `/health` → 200, `/api/build-identity` reports the expected
   `migration.atHead: true`, and a normal login works.

**What a restore cannot undo:** everything written after the dump or after the
restore point. There is no partial or table-level recovery here — a branch
restore "overwrites all data and schema" on that branch.

## Pooled versus direct connections

Neon offers two hostnames for the same endpoint, differing only by a suffix:

```
direct  ep-<id>.<region>.aws.neon.tech
pooled  ep-<id>-pooler.<region>.aws.neon.tech
```

The endpoint ID is identical in both. The pooled one routes through PgBouncer
in transaction mode.

**The application should use whichever Render already has. Migrations,
`pg_dump` and `pg_restore` must use the DIRECT hostname.** Transaction-mode
pooling breaks session-scoped behaviour that Alembic DDL and the dump tools
rely on, and the failure is confusing rather than obvious.

This matters for the deploy specifically: a Pre-Deploy `flask db upgrade`
inherits the service's `DATABASE_URL`. **If that URL is pooled, confirm the
upgrade runs against a direct connection before relying on it.**

## Backup handling

A dump of this database contains every user's data, including coach
conversations. It is not an ops artifact; it is the most concentrated copy of
personal data this project produces.

- Keep the connection string out of shell history. **A leading space does NOT
  do this here** — verified on this machine, `HISTCONTROL=ignoredups`, and
  only `ignorespace` or `ignoreboth` suppress space-prefixed commands. Read it
  from a file, or let the tool prompt for it. Never paste a full
  `DATABASE_URL` into a chat, an issue or a commit — the password sits between
  the first `:` and the **final** `@`.
- Better still, do not put it on a command line at all: arguments are visible
  in `ps` to every process on the box, where history settings are irrelevant.
  `~/backups/streakfit/streakfit-backup.sh` passes connection details as
  libpq **environment variables by name** for exactly this reason.
- Store dumps encrypted and off Neon. Do not leave them in the repository,
  in `/tmp`, or in a cloud folder that syncs.
- **Delete them on a schedule you actually keep.** StreakFit promises 30-day
  deletion of conversations and evidence; a dump taken today still contains
  what was deleted tomorrow, so an undeleted backup silently outlives the
  promise. **This is an unresolved privacy decision, not a solved problem** —
  see `docs/operations/deploy-runbook.md` §8.
- `pg_dump`/`pg_restore` must be the same major version as the Neon server or
  newer, or they refuse to run. Check `SHOW server_version;` against
  `pg_dump --version` before you need it in a hurry.
