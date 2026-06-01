# StreakFit Engine

A production-ready Flask API built to calculate and track streaks for fitness challenges with millisecond precision. Designed for deployment on Render paired with a PostgreSQL database.

## Deployment Details

- **Runtime:** Python
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn app:app`
- **Environment Variables Required:** - `DATABASE_URL`: Your live PostgreSQL connection string.
  - `SECRET_KEY`: Security signature configuration variable.
