# Setup Guide — Local & Production

Zero tribal knowledge: clone → working environment, and the production deploy explained end to end. Reference for variables/secrets: [environment.md](environment.md). What was intentionally left as-is: [reproducibility.md](reproducibility.md).

## Local development — one command

**Prerequisite:** Python **3.12.7** (the pinned runtime). If you don't have it:
```bash
pyenv install 3.12.7 && pyenv local 3.12.7    # recommended
```
> Why exactly 3.12: the pinned `SQLAlchemy==2.0.27` does **not** import on Python 3.14. The `Makefile` checks this and refuses to proceed on the wrong version rather than fail cryptically later.

Then:
```bash
git clone https://github.com/mudmantim/streakfit-api.git
cd streakfit-api
make setup     # venv + deps + .env (generated dev secrets) + local SQLite DB from migrations
make run       # dev server at http://localhost:5000
```

`make setup` is idempotent and does, in order:
1. `check-python` — verify 3.12.x (clear error + pyenv guidance otherwise).
2. `venv` + `install` — create `.venv`, install `requirements-dev.txt`.
3. `env` — if `.env` is missing, copy `.env.example` and fill `SECRET_KEY`/`JWT_SECRET_KEY` with freshly generated values. Leaves an existing `.env` untouched. No Anthropic key by default (coach 503s locally — expected).
4. `db` — `flask db upgrade` builds the local SQLite DB (`streakfit.db`) from the Alembic chain.

### Everyday targets (`make help`)
| Command | Does |
|---|---|
| `make run` | dev server (`python app.py`, port 5000) |
| `make test` | full pytest suite (155 tests) |
| `make check` | what CI runs: install + test |
| `make db` / `make migrate` | apply migrations to the local DB |
| `make revision m="…"` | create a new migration |
| `make verify` | run the end-to-end suite against your local server |
| `make freeze` | print the exact resolved dependency set |
| `make clean` | remove venv/caches/local DB (keeps `.env`) |

### Working on Rickie locally
Add a real `ANTHROPIC_API_KEY` to `.env`. Coach calls are billed. Weather needs nothing. The pytest suite uses a **fake** Anthropic client, so tests never call the API or need a key.

## Production setup (Render)

The live service is git-linked to `main`: **`git push origin main` builds and deploys** (~1 min). There is no staging branch — merging to `main` *is* deploying. A declarative codification of the deploy lives in [`../../render.yaml`](../../render.yaml) (currently **proposed / reconcile-before-adopting** — see its header and [reproducibility.md](reproducibility.md)).

### Build & start
- **Build:** `pip install -r requirements.txt`
- **Start:** `flask db upgrade && STREAKFIT_ENFORCE_DB_HEAD=1 gunicorn app:app`
  - Migrations run first; a failure short-circuits (`&&`) so gunicorn never starts on a bad migration.
  - `STREAKFIT_ENFORCE_DB_HEAD=1` is **inline on gunicorn only** — never a global env var (it would deadlock `flask db upgrade`; see [environment.md](environment.md)).

### First-time provisioning checklist
1. Create the Render web service, git-linked to `mudmantim/streakfit-api` `main`, Python (runtime.txt → 3.12.7).
2. Provision managed Postgres; set `DATABASE_URL`.
3. Set secrets in the dashboard: `SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `ADMIN_SECRET` (all `sync:false`).
4. Set the Build and Start commands above. Confirm the start command binds gunicorn to `$PORT` if Render requires it (**open verification item** — see [reproducibility.md](reproducibility.md)).
5. Set `healthCheckPath` = `/health`.
6. Deploy. Watch the build log; the migration runs first, then the boot guard logs "migration check passed".
7. Verify: `curl -s -o /dev/null -w '%{http_code}' https://streakfit.pro/health` → `200`, then a normal login.

### Routine deploy
```bash
make test                       # green locally (CI also gates this on PRs)
git push origin main            # Render builds + runs the Start Command
# watch Render build log; confirm /health 200 and, for a frontend change, sw.js cache-version flip
```
Rollback, incident, and DB-restore procedures: [../runbooks.md](../runbooks.md).

## Continuous Integration

`.github/workflows/ci.yml` runs on every PR and every push to `main`:
- Sets up **Python 3.12.7** (matches `runtime.txt` — CI tests the real deployed stack).
- Asserts **every** `requirements.txt` line is pinned with `==` (fails otherwise — prevents dependency drift).
- Installs `requirements-dev.txt` and runs `pytest tests/` (includes the migration-chain integrity test).
- Confirms the verification suite imports.

CI never touches production; it gates correctness before a merge/deploy. Because `main` auto-deploys, treat a red CI on a PR as a hard stop before merging.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `make setup` → "python3.12 not found" | Install 3.12.7 (pyenv) or `make setup PYTHON=python3.12`. |
| `RuntimeError` about `SECRET_KEY`/`JWT_SECRET_KEY` | Env not loaded. `make` targets load `.env`; if running manually, `set -a; . ./.env; set +a`. |
| Coach returns 503 locally | No `ANTHROPIC_API_KEY` — expected. Add one to `.env` to work on Rickie. |
| `pip install` fails on SQLAlchemy | You're on Python 3.14. Use 3.12.x (see top of this doc). |
| Tests can't find a table | Local DB not built — `make db`. |
