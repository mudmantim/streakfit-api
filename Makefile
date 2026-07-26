# StreakFit — developer & operations entry points.
# One-command local setup:   make setup   (then `make run` and `make test`).
# Full docs: docs/operations/setup.md

.DEFAULT_GOAL := help
# Target the version prod runs (runtime.txt / .python-version). Override if needed:
#   make setup PYTHON=python3.12
PYTHON ?= python3.12
VENV   := .venv
BIN    := $(VENV)/bin

# Load .env into a recipe's shell (Flask does not auto-load it — no python-dotenv dep).
LOAD_ENV := set -a; [ -f .env ] && . ./.env; set +a
export FLASK_APP := app

.PHONY: help setup check-python venv install env db migrate revision run test check verify freeze clean

help: ## Show this help
	@echo "StreakFit — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: check-python venv install env db ## One command: venv + deps + .env + build local DB
	@echo ""
	@echo "✅ Setup complete. Next:  make run   (dev server)   |   make test   (suite)"

check-python: ## Verify the required Python is available
	@command -v $(PYTHON) >/dev/null 2>&1 || { \
	  echo "ERROR: '$(PYTHON)' not found. StreakFit targets Python 3.12.7 (see runtime.txt)."; \
	  echo "  Install via pyenv:  pyenv install 3.12.7 && pyenv local 3.12.7"; \
	  echo "  Or point make at your interpreter:  make setup PYTHON=python3.12"; \
	  echo "  (The pinned SQLAlchemy 2.0.27 does NOT import on Python 3.14 — use 3.12.x.)"; \
	  exit 1; }
	@$(PYTHON) -c "import sys; v=sys.version_info; \
	  exit(0) if (v.major,v.minor)==(3,12) else (print('WARNING: expected Python 3.12.x, got %d.%d — results may differ from prod'%(v.major,v.minor)) or 0)"

venv: ## Create the virtualenv
	@# Build the venv from the interpreter's REAL path, not the name on PATH.
	@# A symlinked launcher (uv-managed CPython in ~/.local/bin, some pyenv and
	@# Homebrew layouts) makes `python -m venv` record the symlink as the venv's
	@# home. The relocatable standalone build then resolves its own prefix to a
	@# nonexistent '/install', and the very next step dies with an unreadable
	@# "ModuleNotFoundError: No module named 'encodings'" out of ensurepip.
	@# Resolving first is harmless for a normal interpreter and fixes that case.
	@test -d $(VENV) || { \
	  real=$$($(PYTHON) -c 'import os,sys; print(os.path.realpath(getattr(sys,"_base_executable",None) or sys.executable))'); \
	  echo "creating $(VENV) from $$real"; \
	  "$$real" -m venv $(VENV); }
	@# Fail loudly and usefully if the venv is unusable, rather than letting the
	@# next pip call emit a fatal interpreter traceback.
	@$(BIN)/python -c "import encodings, ensurepip" 2>/dev/null || { \
	  echo "ERROR: $(VENV) is broken — its base interpreter cannot bootstrap."; \
	  echo "  Remove it and retry with an explicit interpreter path:"; \
	  echo "    rm -rf $(VENV) && make setup PYTHON=/full/path/to/python3.12"; \
	  exit 1; }
	@$(BIN)/python -m pip install --quiet --upgrade pip

install: venv ## Install runtime + dev dependencies
	@$(BIN)/pip install --quiet -r requirements-dev.txt
	@echo "installed deps into $(VENV)"

env: ## Ensure .env exists and has the secrets the app requires to boot
	@# Top up missing required keys instead of the old all-or-nothing check.
	@# app.py raises at import if SECRET_KEY or JWT_SECRET_KEY is unset, so a
	@# .env that exists but is partial (e.g. a developer who added only
	@# ANTHROPIC_API_KEY) used to make `make setup` skip this step and then die
	@# in `make db` with a raw Flask traceback. Existing values are never
	@# overwritten — only absent or empty keys get a generated dev secret.
	@test -f .env || { touch .env; echo "created empty .env"; }
	@for key in SECRET_KEY JWT_SECRET_KEY; do \
	  if grep -qE "^$$key=." .env; then \
	    echo "$$key already set — leaving it untouched"; \
	  else \
	    val=$$($(BIN)/python -c "import secrets;print(secrets.token_hex(32))"); \
	    grep -vE "^$$key=" .env > .env.tmp && mv .env.tmp .env; \
	    printf '%s=%s\n' "$$key" "$$val" >> .env; \
	    echo "$$key generated (local dev only)"; \
	  fi; \
	done
	@grep -qE '^(DATABASE_URL|ANTHROPIC_API_KEY)=' .env \
	  || echo "note: no DATABASE_URL (SQLite fallback) and no ANTHROPIC_API_KEY (coach returns 503) — fine for local dev; see .env.example"

db: ## Build/upgrade the local database from the Alembic chain
	@$(LOAD_ENV); $(BIN)/flask db upgrade
	@echo "database at Alembic head"

migrate: db ## Alias for `db` (run migrations)

revision: ## Create a new migration (usage: make revision m="add x table")
	@$(LOAD_ENV); $(BIN)/flask db migrate -m "$(m)"

run: ## Run the dev server (http://localhost:5000)
	@$(LOAD_ENV); $(BIN)/python app.py

test: ## Run the full pytest suite
	@$(BIN)/pytest tests/

check: install test ## What CI runs: install + full suite
	@echo "✅ check passed"

verify: ## Run the end-to-end verification suite against a running local server
	@$(BIN)/python scripts/verify_all.py --base-url http://localhost:5000

freeze: ## Print the exact resolved dependency set (for lockfile reconciliation)
	@$(BIN)/pip freeze

clean: ## Remove the virtualenv, caches, and local SQLite DB
	@rm -rf $(VENV) .pytest_cache **/__pycache__ streakfit.db instance
	@echo "cleaned (kept .env)"
