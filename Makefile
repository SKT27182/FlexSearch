.PHONY: help install dev dev-backend deploy-local deploy-backend deploy-frontend stop restart build test clean up down app-restart app-logs backend-local frontend-local backend frontend logs db-shell redis-cli

# Colors
CYAN := \033[36m
RESET := \033[0m

# Paths
BACKEND_VENV := backend/.venv/bin
BACKEND_PYTHON := $(BACKEND_VENV)/python
BACKEND_UVICORN := $(BACKEND_VENV)/uvicorn

# Load environment variables from backend/.env if it exists
ifneq (,$(wildcard ./backend/.env))
    include backend/.env
    export
endif

help: ## Show this help
	@echo "FlexSearch - Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-15s$(RESET) %s\n", $$1, $$2}'

# =============================================================================
# Development
# =============================================================================

install: ## Install all dependencies
	@echo "Installing backend dependencies..."
	cd backend && uv sync
	@echo "Installing frontend dependencies..."
	cd frontend && pnpm install
	@echo "✅ Installation complete!"

dev: dev-backend ## Start backend locally using backend/.env

dev-backend: backend-local ## Start backend with API_PORT from backend/.env

restart: app-restart ## Restart FlexSearch app in Docker

backend-local: ## Start backend server locally (Host must resolve infra-hub hostnames)
	@bash -c 'set -a; [ -f backend/.env ] && source backend/.env; set +a; \
	PYTHONWARNINGS=ignore::UserWarning:multiprocessing.resource_tracker \
	$(BACKEND_UVICORN) app.main:app --reload --port "$${API_PORT:-8889}" --app-dir backend'

frontend-local: ## Start frontend dev server locally
	cd frontend && pnpm run dev

dev-local: ## Start backend + frontend locally (no Docker)
	@echo "Starting backend and frontend locally..."
	@make -j2 backend-local frontend-local

deploy-local: ## Deploy backend + frontend directly (no Docker)
	@echo "Starting backend and frontend deployment servers..."
	@make -j2 deploy-backend deploy-frontend

deploy-backend: ## Run backend server for direct deployment (no Docker)
	@bash -c 'set -a; [ -f backend/.env ] && source backend/.env; set +a; \
	PYTHONWARNINGS=ignore::UserWarning:multiprocessing.resource_tracker \
	$(BACKEND_UVICORN) app.main:app --host 0.0.0.0 --port "$${API_PORT:-8889}" --app-dir backend'

deploy-frontend: ## Build and serve frontend for direct deployment (no Docker)
	@bash -c 'set -a; [ -f frontend/.env ] && source frontend/.env; set +a; \
	cd frontend && pnpm run build && pnpm run preview --host 0.0.0.0 --port "$${VITE_PORT:-5144}"'

# Backwards compatibility / aliases
backend: backend-local
frontend: frontend-local

stop: ## Stop local dev servers
	@echo "Stopping development servers..."
	@pkill -f "uvicorn app.main:app" || true
	@pkill -f "vite" || true
	@echo "✅ Development servers stopped"


# =============================================================================
# Docker (Application)
# =============================================================================
# FlexSearch app containers. Infra services (Postgres, Redis, etc.)
# are provided by infra-hub and must be running on infra-network.

up: ## Build & start FlexSearch app in Docker
	docker compose up -d --build

down: ## Stop FlexSearch app containers
	docker compose down

app-restart: down up ## Restart FlexSearch app containers

app-logs: ## View FlexSearch app logs
	docker compose logs -f

logs: ## View FlexSearch app logs (alias)
	docker compose logs -f


# =============================================================================
# Build & Test
# =============================================================================

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

# =============================================================================
# Database (connects to infra-hub containers)
# =============================================================================

db-migrate: ## Run database migrations
	cd backend && .venv/bin/alembic -c alembic.ini upgrade head

db-revision: ## Create new migration
	@read -p "Migration message: " msg; \
	cd backend && .venv/bin/alembic -c alembic.ini revision --autogenerate -m "$$msg"

db-shell: ## Open PostgreSQL shell (infra-hub)
	docker exec -it infra-postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

redis-cli: ## Open Redis CLI (infra-hub)
	docker exec -it infra-redis redis-cli -a $(REDIS_PASSWORD)


# =============================================================================
# Cleanup
# =============================================================================

clean: ## Clean build artifacts
	rm -rf frontend/dist
	rm -rf backend/.pytest_cache
	rm -rf backend/htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-all: clean down ## Clean everything
	@echo "✅ Cleaned"
