# StreakFit Engine

A production-ready Flask API built to calculate and track streaks for fitness challenges with millisecond precision. Designed for deployment on Render paired with a PostgreSQL database.

## Deployment Details

- **Runtime:** Python
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn app:app`
- **Environment Variables Required:** - `DATABASE_URL`: Your live PostgreSQL connection string.
  - `SECRET_KEY`: Security signature configuration variable.

## Smoke Testing

`scripts/smoke_r2_social.py` runs an end-to-end check of the R2 team/social layer (auth, mission, teams, campfire, moments, chat, Rickie's team reactions, invite rotation, remove member, leave team, unauthorized access) against a live deployment. Standard library only — no `pip install` needed.

It only ever creates new `qa_smoke_*` throwaway accounts and one new team, and never touches any pre-existing user or team, so it's safe to run directly against production:

```bash
python scripts/smoke_r2_social.py https://streakfit.pro
# or: python scripts/smoke_r2_social.py --base-url https://streakfit.pro
# or: SMOKE_BASE_URL=https://streakfit.pro python scripts/smoke_r2_social.py
```

Prints a pass/fail line per check and a summary table, and exits non-zero if anything failed (exit code `2` means a setup step like registration failed and the rest of the run was skipped, since nothing downstream could work without it).
