# StreakFit Engine

A production-ready Flask API built to calculate and track streaks for fitness challenges with millisecond precision. Designed for deployment on Render paired with a PostgreSQL database.

## Quickstart (local)

Requires **Python 3.12.7** (see `runtime.txt`; the pinned SQLAlchemy does not run on 3.14). Then:

```bash
git clone https://github.com/mudmantim/streakfit-api.git && cd streakfit-api
make setup     # venv + deps + .env (generated dev secrets) + local SQLite DB
make run       # http://localhost:5000    |    make test    |    make help
```

Full instructions, production setup, and CI: **[`docs/operations/setup.md`](docs/operations/setup.md)**.
Every environment variable, secret, and startup prerequisite: **[`docs/operations/environment.md`](docs/operations/environment.md)**.
What's reproducible vs. still manual (and why): **[`docs/operations/reproducibility.md`](docs/operations/reproducibility.md)**.
Architecture, ADRs, API spec, roadmaps: **[`docs/architecture/README.md`](docs/architecture/README.md)**.

## Deployment Details

- **Runtime:** Python (Render, PostgreSQL)
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app`
  - Migrations run as an explicit deploy step (a failure stops gunicorn). With
    `STREAKFIT_ENFORCE_DB_HEAD=1` the serving process refuses to boot unless the DB
    is stamped at the Alembic head. See `CLAUDE.md` → *Schema & Migration Integrity*.
- **Environment variables required:**
  - `DATABASE_URL` — live PostgreSQL connection string.
  - `SECRET_KEY` — Flask secret.
  - `JWT_SECRET_KEY` — JWT signing key.
  - `ANTHROPIC_API_KEY` — powers Rickie (the AI coach). Without it, `/api/coach`
    degrades gracefully to `503`; the rest of the app is unaffected.
- **Logging:** the app emits structured `event=… key=value` INFO logs (login,
  coach memory, weather cache hits/misses) in addition to warnings/errors —
  greppable in the Render logs.

## Rickie (AI coach) subsystem

`/api/coach` is a Claude Sonnet 5 coach with cross-session memory and a single
weather tool. The full data flow — memory tables, deterministic Coach Notes
extraction, context injection, the weather cache, deletion ("Forget our
conversations"), and the smoke-account cleanup script — is documented in
**[`docs/memory_pipeline.md`](docs/memory_pipeline.md)**. Rickie's character is
defined in **[`docs/rickie_character_bible.md`](docs/rickie_character_bible.md)**
(the system prompt derives from it and is frozen).

## Verification Suite

Every feature merged into `main` follows the StreakFit Verification Standard (see `CLAUDE.md`) — automated end-to-end coverage lives in `scripts/verification/`, one module per subsystem, run together via `scripts/verify_all.py`. Standard library only, no `pip install` needed, safe to run directly against production (only ever creates throwaway `qa_smoke_*` accounts and one disposable team).

```bash
python scripts/verify_all.py https://streakfit.pro
```

To iterate on one subsystem while developing, run its module directly, e.g. `python scripts/verification/chat.py`. See `scripts/verification/README.md` for the full module list and what's covered.
