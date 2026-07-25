# ADR-0001: Single-file Flask monolith
- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** StreakFit engineering

## Context / Problem
StreakFit's backend is one Flask application (`app.py`, ~4000 lines) that holds
the models, every route, the coach subsystem, the weather tool, the account
deletion service, and the boot-time migration guard. The question is whether to
keep it as a single module or split it into a package (blueprints, a models
package, service modules, etc.).

The relevant facts about this codebase: it is developed by a solo engineer; the
HTTP surface is small (auth, challenges, teams, coach, admin); deployment is a
single Render service running gunicorn against one `app:app` object; and the
test suite (`tests/`) imports `app` directly.

## Decision
Keep the backend as a single `app.py` module. Everything the running server
needs is defined there and imported as `app.app`, `app.User`,
`app.delete_user_account`, etc. The only code deliberately kept *out* of import
time is the verification/test tooling, which is imported lazily inside the
admin verify routes so the serving app is never coupled to test code
(`app.py:24-26`).

## Alternatives considered
- **Blueprint-per-domain package** (`auth/`, `teams/`, `coach/`, `admin/`).
  What it was: split routes into Flask blueprints registered on the app, models
  into a `models` module. Why it lost: it multiplies import wiring and
  circular-import risk for a surface this small, and buys navigability that a
  single well-sectioned file (with `# ---` banners) already provides for one
  author. The cost is real; the payoff at this scale is not.
- **Service/repository layering** (thin routes calling a service layer calling
  repositories). What it was: the standard enterprise separation. Why it lost:
  the app already isolates the genuinely reusable logic as plain functions in
  the same module (`delete_user_account`, `_persist_coach_interaction`,
  `_weather_tool_result`), which the CLI scripts and tests call directly. A
  formal layer would add indirection without adding a second real consumer.
- **Full framework migration** (e.g. FastAPI + Pydantic + async). Why it lost:
  a rewrite with no product driver; the current stack (Flask + SQLAlchemy +
  Flask-JWT-Extended + Flask-Limiter) is boring, well-understood, and deployed.

## Why the current solution won
For a solo dev with a small surface, one module is the lowest-friction option:
one file to open, one grep to find anything, one object to deploy, and zero
package plumbing. The parts that actually needed to be reusable were extracted
as functions, not modules, so scripts and tests reuse them without a package
boundary. The decision optimizes for the real constraint (one person's working
memory and a simple deploy) rather than a hypothetical team.

## Consequences & future tradeoffs
- **Makes easy:** finding code (single grep target), deploying (one `app:app`),
  reusing logic from scripts/tests (direct function import), and reasoning about
  import order.
- **Makes hard:** navigation grows harder as the file grows; a multi-author
  workflow would see merge conflicts concentrate in this one file; and importing
  `app` for a test pays the cost of loading the *entire* backend (all routes,
  the exercise library, the coach prompt) even for a unit test of one helper.
- **When we'd revisit:** a second regular contributor (merge-conflict pain), the
  file crossing a size where grep-navigation breaks down, or a need to run part
  of the backend (e.g. the coach) as an independently deployable service. The
  natural first cut is the coach subsystem, which is already function-clustered.

## Code references
- `app.py:28` — `app = Flask(__name__)`, the single application object.
- `app.py:24-26` — comment recording that verification/test code is imported
  lazily in the admin routes, not at module import time.
- `app.py:1100-1296` — the model definitions (`User`, `Team`, `TeamMessage`,
  `TeamMoment`, `CoachTurn`, `CoachNote`, …) that share the module with routes.
- `app.py:4017-4018` — `app.run(...)` under `__main__`; the module *is* the app.
- See [`../architecture/README.md`](../architecture/README.md) for the current
  module map and [`../architecture/deployment.md`](../architecture/deployment.md)
  for how the single object is served.
