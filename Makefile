SHELL := /bin/bash

.PHONY: help install dev-local dev up down clean clean-all clean-hard print-urls prepare-logs stop-local wait-db logs logs-local \
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
BACKEND_ENV_FILE := $(if $(wildcard backend/.env),backend/.env,backend/.env.example)
FRONTEND_ENV_FILE := $(if $(wildcard frontend/.env),frontend/.env,frontend/.env.example)
APP_HOST_RAW := $(shell awk -F= '/^SERVICE_PUBLIC_HOST=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)
APP_HOST := $(if $(APP_HOST_RAW),$(APP_HOST_RAW),127.0.0.1)
BACKEND_PORT_RAW := $(shell awk -F= '/^API_PORT=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)
BACKEND_PORT := $(if $(BACKEND_PORT_RAW),$(BACKEND_PORT_RAW),8889)
FRONTEND_PORT_RAW := $(shell awk -F= '/^VITE_PORT=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(FRONTEND_ENV_FILE) 2>/dev/null)
FRONTEND_PORT := $(if $(FRONTEND_PORT_RAW),$(FRONTEND_PORT_RAW),5144)
INFRA_HUB_DIR := ../infra-hub
INFRA_HUB_PERSIST_DIR := $(INFRA_HUB_DIR)/volumes
INFRA_POSTGRES_CONTAINER ?= infra-postgres

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
	@$(MAKE) --no-print-directory stop-local
	@$(MAKE) --no-print-directory wait-db
	@echo "backend log:  $(BACKEND_LOG)"
	@echo "frontend log: $(FRONTEND_LOG)"
	@$(MAKE) --no-print-directory print-urls
	@bash -c 'set -euo pipefail; \
		trap '"'"'kill $$backend_pid $$frontend_pid 2>/dev/null || true; rm -f "$(BACKEND_PID)" "$(FRONTEND_PID)"'"'"' INT TERM EXIT; \
		( set -a; [ -f backend/.env ] && source backend/.env; set +a; \
		  PYTHONWARNINGS=ignore::UserWarning:multiprocessing.resource_tracker \
		  $(BACKEND_UVICORN) app.main:app --reload --port "$${API_PORT:-$(BACKEND_PORT)}" --app-dir backend \
		) >> "$(BACKEND_LOG)" 2>&1 & backend_pid=$$!; echo $$backend_pid > "$(BACKEND_PID)"; \
		( cd frontend && pnpm run dev ) >> "$(FRONTEND_LOG)" 2>&1 & frontend_pid=$$!; echo $$frontend_pid > "$(FRONTEND_PID)"; \
		wait $$backend_pid $$frontend_pid'

up: ## Start app containers in Docker
	docker compose up -d --build
	@$(MAKE) --no-print-directory print-urls

dev: up ## Run with Docker

stop-local: ## Stop locally started backend/frontend processes from pid files
	@if [ -f "$(BACKEND_PID)" ]; then kill "$$(cat "$(BACKEND_PID)")" 2>/dev/null || true; rm -f "$(BACKEND_PID)"; fi
	@if [ -f "$(FRONTEND_PID)" ]; then kill "$$(cat "$(FRONTEND_PID)")" 2>/dev/null || true; rm -f "$(FRONTEND_PID)"; fi
	@for port in "$(BACKEND_PORT)" "$(FRONTEND_PORT)"; do \
		pids="$$(ss -ltnp | awk -v p=":$$port$$" '$$4 ~ p {print}' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)"; \
		if [ -n "$$pids" ]; then \
			echo "Stopping processes on port $$port: $$pids"; \
			kill $$pids 2>/dev/null || true; \
		fi; \
	done

wait-db: ## Wait for shared infra postgres to become healthy
	@echo "Waiting for shared PostgreSQL container to be healthy..."
	@timeout 90 bash -c 'set -euo pipefail; \
		container="$(INFRA_POSTGRES_CONTAINER)"; \
		until docker inspect "$$container" >/dev/null 2>&1; do sleep 1; done; \
		while true; do \
			status=$$(docker inspect -f "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" "$$container" 2>/dev/null || true); \
			if [ "$$status" = "healthy" ] || [ "$$status" = "running" ]; then \
				break; \
			fi; \
			sleep 1; \
		done'

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

db-migrate: ## Run database migrations (idempotent; safe on existing DBs)
	cd backend && .venv/bin/alembic -c alembic.ini upgrade head

db-stamp: ## Mark DB as migrated without running SQL (schema already current via init_db)
	cd backend && .venv/bin/alembic -c alembic.ini stamp head

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

clean-hard: stop-local ## Force cleanup: stop/remove containers, networks, volumes, and local logs
	rm -f "$(BACKEND_LOG)" "$(FRONTEND_LOG)"
	docker compose down --volumes --remove-orphans --rmi local
	@if [ -f "$(INFRA_HUB_DIR)/docker-compose.yml" ]; then \
		docker compose -f "$(INFRA_HUB_DIR)/docker-compose.yml" down --volumes --remove-orphans --rmi local; \
	fi
	@rm -rf "$(INFRA_HUB_PERSIST_DIR)"

print-urls: ## Print frontend/backend URLs from env-configured ports
	@echo "Backend URL:  http://$(APP_HOST):$(BACKEND_PORT)"
	@echo "Frontend URL: http://$(APP_HOST):$(FRONTEND_PORT)"
