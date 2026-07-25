# ADR-0010: Migrations as the single source of truth + boot guard
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
The schema was managed by Alembic (Flask-Migrate), but the guarantee that
migrations actually *build* the schema had silently failed. The baseline
migration shipped as a no-op (`pass`/`pass`) because, when it was stamped, the
`user` and `challenge` tables already existed in the live DB (they predate
Alembic here). So nothing in the chain ever created `user` or `challenge`, and a
brand-new database could not be built from migrations at all — the next revision
immediately did `add_column('user', …)` against a table that was never created.
This stayed invisible for weeks because the rest of the test suite builds its
test DB with `create_all()`, bypassing Alembic entirely. Separately, migrations
had been run *inside the app on boot* — a silent, error-swallowing auto-migrate
that raced across gunicorn workers and could boot a broken/missing schema
straight into runtime 500s.

## Decision
Make the Alembic migration chain the single source of truth for the schema, and
guard it at two points:

- **No `create_all()` and no auto-migrate on boot in production.** A fresh
  database is built entirely by `flask db upgrade`. The backfilled baseline
  (`a3f8b1c2d4e5_baseline.py`) now creates the pre-Alembic `user` (baseline-era
  columns only — later columns are added by later revisions) and `challenge`
  tables, so the chain builds the complete schema from empty.
- **Migrations run as an explicit deploy step.** The Render Start Command is
  `flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app`: the upgrade
  runs once per deploy and, if it fails, `&&` stops gunicorn from starting.
- **Boot guard: refuse to serve against a wrong schema.** When
  `STREAKFIT_ENFORCE_DB_HEAD=1`, `_assert_db_at_head()` compares the DB's current
  Alembic revision to the code's head and `raise SystemExit(1)` if they differ or
  the DB is unreachable — the serving process refuses to start rather than serve
  a wrong/missing schema. The env-var gate keeps this from firing during the
  `flask db upgrade` step itself and during tests/local dev.
- **Parity enforced by test.** `tests/test_migrations.py` runs `flask db upgrade`
  against an empty DB and asserts the result matches the models (tables, columns,
  PKs, FKs, unique constraints, indexes; server-defaults excluded by design), so
  models and migrations can no longer drift without CI failing.

## Alternatives considered
- **`create_all()` on boot / as the schema source.** Why it lost: it bypasses
  Alembic, so migrations and models drift undetected — exactly how the no-op
  baseline hid for weeks. `create_all()` builds from models, not the chain, so it
  masks a broken chain rather than exercising it.
- **Auto-migrate inside the app on boot** (the previous behavior). Why it lost:
  it swallowed errors, raced across workers, and could boot a half-migrated
  schema into runtime 500s. Migration is a deploy concern, not a per-worker
  runtime concern.
- **Trust the deploy step, no boot guard.** Why it lost: without
  `STREAKFIT_ENFORCE_DB_HEAD`, a process could still come up against a DB that
  isn't at head (a skipped/failed upgrade, a rolled-back DB) and serve wrong-schema
  500s. Refusing to boot turns that into a loud, immediate failure.
- **Rely on manual review to keep models and migrations in sync.** Why it lost:
  the original drift was invisible to review for weeks; only an automated
  from-empty parity test closes the gap permanently.

## Why the current solution won
The combination makes "the schema is what the migrations say" a *checked*
invariant rather than an assumption. The from-empty parity test proves the chain
actually builds the model schema (the thing that silently wasn't true); moving
migration to an explicit deploy step removes the racing, error-swallowing
boot-time auto-migrate; and the head-enforcement guard makes a mismatched schema
fail loudly at startup instead of quietly at request time. Each piece closes a
specific, real failure that had already happened.

## Consequences & future tradeoffs
- **Makes easy:** rebuilding a database from empty with confidence; catching
  model/migration drift in CI; and a hard, early failure when the DB isn't at
  head, instead of scattered runtime 500s.
- **Makes hard:** every schema change is now a two-part commitment — model **and**
  migration — that must pass the from-empty parity test before it's "done" (per
  the Verification Standard in `CLAUDE.md`). The deploy is coupled to a
  successful `flask db upgrade`, and a forgotten/failed upgrade will (correctly)
  refuse to boot, which is safer but less forgiving than serving anyway.
- **When we'd revisit:** the deferred `ondelete` constraints migration
  ([ADR-0006](0006-application-level-account-deletion.md)) will exercise this
  whole discipline — model changes plus a batch-rebuild migration plus the parity
  test. If migrations ever need to run somewhere other than the deploy step, the
  guard's env-var gating is the seam to adjust.

## Code references
- `app.py:3971-3985` — comment documenting the deploy-step Start Command
  (`flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app`) and why
  boot-time auto-migrate was removed.
- `app.py:3987-4010` — `_assert_db_at_head`: compares DB revision to Alembic head,
  `SystemExit(1)` on mismatch or unreachable DB.
- `app.py:4013-4014` — the `STREAKFIT_ENFORCE_DB_HEAD == '1'` gate that runs the
  guard only on the serving process.
- `migrations/versions/a3f8b1c2d4e5_baseline.py` — backfilled baseline creating
  `user` (baseline-era columns) and `challenge`; docstring records the no-op
  history.
- `tests/test_migrations.py` — from-empty `flask db upgrade` == model schema
  parity test.
- See `CLAUDE.md` (Schema & Migration Integrity) and
  [`../architecture/deployment.md`](../architecture/deployment.md),
  [`../architecture/verification-suite.md`](../architecture/verification-suite.md).
