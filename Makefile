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
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(BIN)/python -m pip install --quiet --upgrade pip

install: venv ## Install runtime + dev dependencies
	@$(BIN)/pip install --quiet -r requirements-dev.txt
	@echo "installed deps into $(VENV)"

env: ## Create .env from .env.example with generated dev secrets (if missing)
	@if [ -f .env ]; then \
	  echo ".env already exists — leaving it untouched"; \
	else \
	  cp .env.example .env; \
	  sk=$$($(BIN)/python -c "import secrets;print(secrets.token_hex(32))" 2>/dev/null || $(PYTHON) -c "import secrets;print(secrets.token_hex(32))"); \
	  jk=$$($(BIN)/python -c "import secrets;print(secrets.token_hex(32))" 2>/dev/null || $(PYTHON) -c "import secrets;print(secrets.token_hex(32))"); \
	  sed -i.bak "s|^SECRET_KEY=.*|SECRET_KEY=$$sk|" .env && rm -f .env.bak; \
	  sed -i.bak "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$$jk|" .env && rm -f .env.bak; \
	  echo "created .env with generated dev secrets (SQLite fallback, no Anthropic key)"; \
	fi

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
