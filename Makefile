SHELL := /bin/bash

.PHONY: help install dev-local worker-local dev up down clean clean-all clean-hard print-urls prepare-logs stop-local wait-db db-bootstrap logs logs-local \
	build test test-cov eval lint format db-migrate db-revision db-shell redis-cli

CYAN := \033[36m
RESET := \033[0m

BACKEND_VENV := backend/.venv/bin
BACKEND_UVICORN := $(BACKEND_VENV)/uvicorn
HOST_OS ?= $(shell uname -s 2>/dev/null || echo unknown)
ifeq ($(HOST_OS),Darwin)
# PyTorch MPS and other macOS native frameworks can abort in forked children.
CELERY_POOL ?= solo
CELERY_CONCURRENCY ?= 1
else
CELERY_POOL ?= prefork
CELERY_CONCURRENCY ?= 2
endif
LOG_DIR := $(HOME)/.local/share/projects/flexsearch/dev-logs
DATA_DIR := $(HOME)/.local/share/projects/flexsearch
BACKEND_LOG := $(LOG_DIR)/backend.log
FRONTEND_LOG := $(LOG_DIR)/frontend.log
BACKEND_PID := $(LOG_DIR)/backend.pid
FRONTEND_PID := $(LOG_DIR)/frontend.pid
WORKER_PID := $(LOG_DIR)/worker.pid
WORKER_LOG := $(LOG_DIR)/worker.log
CELERY_BEAT_DB := $(DATA_DIR)/celerybeat-schedule.db
# DEV_LOG_MODE: file | console | both (default)
DEV_LOG_MODE ?= both
BACKEND_ENV_FILE := $(if $(wildcard backend/.env),backend/.env,backend/.env.example)
FRONTEND_ENV_FILE := $(if $(wildcard frontend/.env),frontend/.env,frontend/.env.example)
APP_HOST_RAW := $(shell awk -F= '/^SERVICE_PUBLIC_HOST=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)
APP_HOST := $(if $(APP_HOST_RAW),$(APP_HOST_RAW),127.0.0.1)
BACKEND_PORT_RAW := $(shell awk -F= '/^API_PORT=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)
BACKEND_PORT := $(if $(BACKEND_PORT_RAW),$(BACKEND_PORT_RAW),8889)
FRONTEND_PORT_RAW := $(shell awk -F= '/^VITE_PORT=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(FRONTEND_ENV_FILE) 2>/dev/null)
FRONTEND_PORT := $(if $(FRONTEND_PORT_RAW),$(FRONTEND_PORT_RAW),5144)
INFRA_POSTGRES_CONTAINER ?= infra-postgres

POSTGRES_USER_RAW := $(shell awk -F= '/^POSTGRES_USER=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)
POSTGRES_USER := $(POSTGRES_USER_RAW)
POSTGRES_DB_RAW := $(shell awk -F= '/^POSTGRES_DB=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)
POSTGRES_DB := $(if $(POSTGRES_DB_RAW),$(POSTGRES_DB_RAW),flexsearch)
REDIS_PASSWORD := $(shell awk -F= '/^REDIS_PASSWORD=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)

help: ## Show this help
	@printf '%s\n' "FlexSearch - Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-15s$(RESET) %s\n", $$1, $$2}'

install: ## Install backend (uv sync) and frontend (pnpm install) dependencies
	cd backend && uv sync
	cd frontend && pnpm install

prepare-logs:
	@mkdir -p "$(LOG_DIR)"
	@if [ "$(DEV_LOG_MODE)" != "console" ]; then \
		: > "$(BACKEND_LOG)"; \
		: > "$(FRONTEND_LOG)"; \
		: > "$(WORKER_LOG)"; \
	fi

dev-local: install prepare-logs ## Run backend + frontend + Celery worker locally
	@$(MAKE) --no-print-directory stop-local
	@$(MAKE) --no-print-directory wait-db
	@$(MAKE) --no-print-directory db-bootstrap
	@echo "log mode: $(DEV_LOG_MODE)"
	@if [ "$(DEV_LOG_MODE)" != "console" ]; then \
		echo "backend log:  $(BACKEND_LOG)"; \
		echo "frontend log: $(FRONTEND_LOG)"; \
		echo "worker log:   $(WORKER_LOG)"; \
	fi
	@$(MAKE) --no-print-directory print-urls
	@bash -c 'set -euo pipefail; \
		log_mode="$(DEV_LOG_MODE)"; \
		case "$$log_mode" in file|console|both) ;; \
			*) echo "Invalid DEV_LOG_MODE: $$log_mode (use file, console, or both)" >&2; exit 1 ;; \
		esac; \
		setup_log_pipe() { \
			local logfile="$$1"; \
			case "$$log_mode" in \
				console) ;; \
				both) exec > >(tee -a "$$logfile") 2>&1 ;; \
				file|*) exec >> "$$logfile" 2>&1 ;; \
			esac; \
		}; \
		trap '"'"'kill $$backend_pid $$frontend_pid $$worker_pid 2>/dev/null || true; rm -f "$(BACKEND_PID)" "$(FRONTEND_PID)" "$(WORKER_PID)"'"'"' INT TERM EXIT; \
		( setup_log_pipe "$(BACKEND_LOG)"; set -a; [ -f backend/.env ] && source backend/.env; set +a; \
		  PYTHONWARNINGS=ignore::UserWarning:multiprocessing.resource_tracker \
		  $(BACKEND_UVICORN) app.main:app --reload --port "$${API_PORT:-$(BACKEND_PORT)}" --app-dir backend \
		) & backend_pid=$$!; echo $$backend_pid > "$(BACKEND_PID)"; \
		( setup_log_pipe "$(WORKER_LOG)"; set -a; [ -f backend/.env ] && source backend/.env; set +a; \
		  cd backend && .venv/bin/celery -A app.celery_app worker --beat --loglevel=INFO \
		    --schedule="$(CELERY_BEAT_DB)" \
		    -Q ingest,graph,summary,default --pool="$(CELERY_POOL)" --concurrency="$(CELERY_CONCURRENCY)" \
		) & worker_pid=$$!; echo $$worker_pid > "$(WORKER_PID)"; \
		( setup_log_pipe "$(FRONTEND_LOG)"; cd frontend && pnpm run dev ) & frontend_pid=$$!; echo $$frontend_pid > "$(FRONTEND_PID)"; \
		wait $$backend_pid $$frontend_pid $$worker_pid'

worker-local: ## Run Celery worker and local outbox scheduler
	cd backend && .venv/bin/celery -A app.celery_app worker --beat --loglevel=INFO \
		--schedule="$(CELERY_BEAT_DB)" \
		-Q ingest,graph,summary,default --pool="$(CELERY_POOL)" --concurrency="$(CELERY_CONCURRENCY)"

up: ## Start app containers in Docker
	docker compose up -d --build
	@$(MAKE) --no-print-directory print-urls

dev: up ## Run with Docker

stop-local: ## Stop locally started backend/frontend/worker processes from pid files
	@if [ -f "$(BACKEND_PID)" ]; then kill "$$(cat "$(BACKEND_PID)")" 2>/dev/null || true; rm -f "$(BACKEND_PID)"; fi
	@if [ -f "$(FRONTEND_PID)" ]; then kill "$$(cat "$(FRONTEND_PID)")" 2>/dev/null || true; rm -f "$(FRONTEND_PID)"; fi
	@if [ -f "$(WORKER_PID)" ]; then kill "$$(cat "$(WORKER_PID)")" 2>/dev/null || true; rm -f "$(WORKER_PID)"; fi
	@# Use [c]elery so pkill -f does not match this make/shell recipe (self-SIGTERM).
	@pkill -f "[c]elery -A app.celery_app" 2>/dev/null || true
	@if ! command -v lsof >/dev/null 2>&1; then \
		echo "WARNING: lsof is not installed; skipping port-based process cleanup." >&2; \
		exit 0; \
	fi; \
	for port in "$(BACKEND_PORT)" "$(FRONTEND_PORT)"; do \
		pids="$$(lsof -nP -tiTCP:"$$port" -sTCP:LISTEN 2>/dev/null)"; \
		if [ -n "$$pids" ]; then \
			echo "Stopping processes on port $$port: $$pids"; \
			kill $$pids 2>/dev/null || true; \
			sleep 1; \
			pids="$$(lsof -nP -tiTCP:"$$port" -sTCP:LISTEN 2>/dev/null)"; \
			if [ -n "$$pids" ]; then \
				echo "Force stopping processes on port $$port: $$pids"; \
				kill -9 $$pids 2>/dev/null || true; \
			fi; \
		fi; \
	done

wait-db: ## Wait for shared infra postgres to become healthy and accept credentials
	@echo "Waiting for shared PostgreSQL container to be healthy..."
	@set -eu; \
		container="$(INFRA_POSTGRES_CONTAINER)"; \
		deadline=$$(( $$(date +%s) + 90 )); \
		until docker inspect "$$container" >/dev/null 2>&1; do \
			if [ "$$(date +%s)" -ge "$$deadline" ]; then \
				echo "ERROR: PostgreSQL container $$container was not found within 90 seconds." >&2; \
				exit 1; \
			fi; \
			sleep 1; \
		done; \
		while true; do \
			status=$$(docker inspect -f "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" "$$container" 2>/dev/null || true); \
			if [ "$$status" = "healthy" ] || [ "$$status" = "running" ]; then \
				break; \
			fi; \
			if [ "$$(date +%s)" -ge "$$deadline" ]; then \
				echo "ERROR: PostgreSQL container $$container was not ready within 90 seconds (status: $$status)." >&2; \
				exit 1; \
			fi; \
			sleep 1; \
		done
	@echo "Verifying PostgreSQL credentials from backend/.env..."
	@POSTGRES_PASSWORD="$$(awk -F= '/^POSTGRES_PASSWORD=/{gsub(/^[ \t]+|[ \t]+$$/,"",$$2); print $$2; exit}' $(BACKEND_ENV_FILE) 2>/dev/null)"; \
	if [ -z "$(POSTGRES_USER)" ] || [ -z "$$POSTGRES_PASSWORD" ]; then \
		echo "ERROR: POSTGRES_USER / POSTGRES_PASSWORD must be set in $(BACKEND_ENV_FILE)" >&2; \
		exit 1; \
	fi; \
	deadline=$$(( $$(date +%s) + 60 )); \
	until docker exec -e PGPASSWORD="$$POSTGRES_PASSWORD" "$(INFRA_POSTGRES_CONTAINER)" \
			psql -U "$(POSTGRES_USER)" -d postgres -tAc "SELECT 1" >/dev/null 2>&1; do \
		if [ "$$(date +%s)" -ge "$$deadline" ]; then \
			echo "ERROR: cannot authenticate to Postgres with POSTGRES_* from $(BACKEND_ENV_FILE) within 60 seconds." >&2; \
			echo "If the password changed, reset infra data: cd ../infra-hub && make clean-hard && make up" >&2; \
			exit 1; \
		fi; \
		sleep 1; \
	done

db-bootstrap: ## Create app DB if needed and run Alembic migrations
	@echo "Bootstrapping FlexSearch database..."
	cd backend && .venv/bin/python -m scripts.db_bootstrap

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

eval: ## Run golden-set RAG eval harness (CI-safe, offline)
	cd backend && .venv/bin/python -m app.eval --k 5 --min-hit-at-k 0.8 --min-faithfulness 0.5

test-cov: ## Run tests with coverage
	cd backend && .venv/bin/pytest tests/ -v --cov=app --cov-report=html

lint: ## Lint backend code
	$(BACKEND_VENV)/ruff check backend/app/

format: ## Format backend code
	$(BACKEND_VENV)/ruff format backend/app/

db-migrate: ## Run database migrations (idempotent; safe on existing DBs)
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

clean-all: clean ## Remove this project's logs and Docker resources (including volumes)
	rm -rf "$(LOG_DIR)"
	docker compose down -v --remove-orphans

clean-hard: stop-local ## Force cleanup of FlexSearch only (does not touch infra-hub shared services)
	rm -rf "$(LOG_DIR)"
	docker compose down --volumes --remove-orphans --rmi local

print-urls: ## Print frontend/backend URLs from env-configured ports
	@echo "Backend URL:  http://$(APP_HOST):$(BACKEND_PORT)"
	@echo "Frontend URL: http://$(APP_HOST):$(FRONTEND_PORT)"
	@echo "Data dir:     $(DATA_DIR)"
	@echo "Logs dir:     $(LOG_DIR)"
