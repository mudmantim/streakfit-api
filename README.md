# StreakFit Engine

A production-ready Flask API built to calculate and track streaks for fitness challenges with millisecond precision. Designed for deployment on Render paired with a PostgreSQL database.

## Deployment Details

- **Runtime:** Python
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn app:app`
- **Required Environment Variables:**
  - `DATABASE_URL`: PostgreSQL connection string. The app falls back to local SQLite when omitted.
  - `SECRET_KEY`: Flask application secret.
  - `JWT_SECRET_KEY`: Secret used to sign JWT access tokens.

## Verification Suite

Every feature merged into `main` follows the StreakFit Verification Standard (see `CLAUDE.md`) — automated end-to-end coverage lives in `scripts/verification/`, one module per subsystem, run together via `scripts/verify_all.py`. Standard library only, no `pip install` needed, safe to run directly against production (only ever creates throwaway `qa_smoke_*` accounts and one disposable team).

```bash
python scripts/verify_all.py https://streakfit.pro
```

To iterate on one subsystem while developing, run its module directly, e.g. `python scripts/verification/chat.py`. See `scripts/verification/README.md` for the full module list and what's covered.
