SHELL := /bin/bash

.PHONY: help install dev-local dev up down clean clean-all prepare-logs stop-local logs logs-local \
	build test test-cov lint format db-migrate db-revision db-shell redis-cli

CYAN := \033[36m
RESET := \033[0m

BACKEND_VENV := backend/.venv/bin
BACKEND_UVICORN := $(BACKEND_VENV)/uvicorn
LOG_DIR := $(HOME)/.local/share/dev-logs/flexsearch
BACKEND_LOG := $(LOG_DIR)/backend.log
FRONTEND_LOG := $(LOG_DIR)/frontend.log
BACKEND_PID := $(LOG_DIR)/backend.pid
FRONTEND_PID := $(LOG_DIR)/frontend.pid

help: ## Show this help
	@echo "FlexSearch - Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-15s$(RESET) %s\n", $$1, $$2}'

install: ## Install backend (uv sync) and frontend (pnpm install) dependencies
	cd backend && uv sync
	cd frontend && pnpm install

prepare-logs:
	@mkdir -p "$(LOG_DIR)"
	@: > "$(BACKEND_LOG)"
	@: > "$(FRONTEND_LOG)"

dev-local: install prepare-logs ## Run backend + frontend locally (no Docker), with log files
	@echo "backend log:  $(BACKEND_LOG)"
	@echo "frontend log: $(FRONTEND_LOG)"
	@bash -c 'set -euo pipefail; \
		trap '"'"'kill $$backend_pid $$frontend_pid 2>/dev/null || true; rm -f "$(BACKEND_PID)" "$(FRONTEND_PID)"'"'"' INT TERM EXIT; \
		( set -a; [ -f backend/.env ] && source backend/.env; set +a; \
		  PYTHONWARNINGS=ignore::UserWarning:multiprocessing.resource_tracker \
		  $(BACKEND_UVICORN) app.main:app --reload --port "$${API_PORT:-8889}" --app-dir backend \
		) >> "$(BACKEND_LOG)" 2>&1 & backend_pid=$$!; echo $$backend_pid > "$(BACKEND_PID)"; \
		( cd frontend && pnpm run dev ) >> "$(FRONTEND_LOG)" 2>&1 & frontend_pid=$$!; echo $$frontend_pid > "$(FRONTEND_PID)"; \
		wait $$backend_pid $$frontend_pid'

up: ## Start app containers in Docker
	docker compose up -d --build

dev: up ## Run with Docker

stop-local: ## Stop locally started backend/frontend processes from pid files
	@if [ -f "$(BACKEND_PID)" ]; then kill "$$(cat "$(BACKEND_PID)")" 2>/dev/null || true; rm -f "$(BACKEND_PID)"; fi
	@if [ -f "$(FRONTEND_PID)" ]; then kill "$$(cat "$(FRONTEND_PID)")" 2>/dev/null || true; rm -f "$(FRONTEND_PID)"; fi

down: stop-local ## Stop Docker app and local dev processes
	docker compose down

logs: ## View Docker logs
	docker compose logs -f

logs-local: ## Tail local backend/frontend log files
	@tail -f "$(BACKEND_LOG)" "$(FRONTEND_LOG)"

build: ## Build frontend for production
	cd frontend && pnpm run build

test: ## Run backend tests
	cd backend && .venv/bin/pytest tests/ -v

test-cov: ## Run tests with coverage
	cd backend && .venv/bin/pytest tests/ -v --cov=app --cov-report=html

lint: ## Lint backend code
	$(BACKEND_VENV)/ruff check backend/app/

format: ## Format backend code
	$(BACKEND_VENV)/ruff format backend/app/

db-migrate: ## Run database migrations
	cd backend && .venv/bin/alembic -c alembic.ini upgrade head

db-revision: ## Create new migration
	@read -p "Migration message: " msg; \
	cd backend && .venv/bin/alembic -c alembic.ini revision --autogenerate -m "$$msg"

db-shell: ## Open PostgreSQL shell (infra-hub)
	docker exec -it infra-postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

redis-cli: ## Open Redis CLI (infra-hub)
	docker exec -it infra-redis redis-cli -a $(REDIS_PASSWORD)

clean: stop-local ## Clean local artifacts and pid files
	rm -rf frontend/dist backend/.pytest_cache backend/htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-all: clean ## Remove logs and Docker resources (including volumes)
	rm -f "$(BACKEND_LOG)" "$(FRONTEND_LOG)"
	docker compose down -v --remove-orphans
