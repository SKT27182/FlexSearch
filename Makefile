.PHONY: help install dev stop restart build test clean docker-up docker-down docker-restart docker-clean backend frontend logs db-shell redis-cli

# Colors
CYAN := \033[36m
RESET := \033[0m

# Paths
BACKEND_VENV := backend/.venv/bin
BACKEND_PYTHON := $(BACKEND_VENV)/python
BACKEND_UVICORN := $(BACKEND_VENV)/uvicorn

help: ## Show this help
	@echo "FlexSearch - Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-15s$(RESET) %s\n", $$1, $$2}'

# =============================================================================
# Development
# =============================================================================

install: ## Install all dependencies
	@echo "Creating backend virtual environment..."
	cd backend && uv venv
	@echo "Installing backend dependencies..."
	cd backend && uv pip install -e .
	@echo "Installing frontend dependencies..."
	cd frontend && pnpm install
	@echo "✅ Installation complete!"

dev: docker-up ## Start all services for development
	@echo "Starting backend and frontend..."
	@make -j2 backend frontend

backend: ## Start backend server
	@PYTHONWARNINGS=ignore::UserWarning:multiprocessing.resource_tracker \
	$(BACKEND_UVICORN) app.main:app --reload --port 8000 --app-dir backend

frontend: ## Start frontend dev server
	cd frontend && pnpm run dev

stop: ## Stop all running services gracefully
	@echo "Stopping development servers..."
	@pkill -f "uvicorn app.main:app" || true
	@pkill -f "vite" || true
	@echo "✅ Development servers stopped"

restart: stop docker-down ## Restart all services
	@echo "Restarting all services..."
	@sleep 2
	@make dev


# =============================================================================
# Docker
# =============================================================================

docker-up: ## Start infrastructure (PostgreSQL, Redis, Qdrant, MinIO)
	cd docker && docker compose up -d

docker-down: ## Stop infrastructure
	cd docker && docker compose down

docker-restart: docker-down docker-up ## Restart infrastructure

docker-clean: ## Stop infrastructure and remove volumes
	cd docker && docker compose down -v

docker-logs: ## View infrastructure logs
	cd docker && docker compose logs -f

logs: ## View all logs (infrastructure + app)
	cd docker && docker compose logs -f


# =============================================================================
# Build & Test
# =============================================================================

build: ## Build frontend for production
	cd frontend && pnpm run build

test: ## Run backend tests
	cd backend && $(BACKEND_VENV)/pytest tests/ -v

test-cov: ## Run tests with coverage
	cd backend && $(BACKEND_VENV)/pytest tests/ -v --cov=app --cov-report=html

lint: ## Lint backend code
	$(BACKEND_VENV)/ruff check backend/app/

format: ## Format backend code
	$(BACKEND_VENV)/ruff format backend/app/

# =============================================================================
# Database
# =============================================================================

db-migrate: ## Run database migrations
	$(BACKEND_VENV)/alembic -c backend/alembic.ini upgrade head

db-revision: ## Create new migration
	@read -p "Migration message: " msg; \
	$(BACKEND_VENV)/alembic -c backend/alembic.ini revision --autogenerate -m "$$msg"

db-shell: ## Open PostgreSQL shell
	docker exec -it flexsearch-postgres psql -U flexsearch -d flexsearch

redis-cli: ## Open Redis CLI
	docker exec -it flexsearch-redis redis-cli


# =============================================================================
# Cleanup
# =============================================================================

clean: ## Clean build artifacts
	rm -rf frontend/dist
	rm -rf backend/.pytest_cache
	rm -rf backend/htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-all: clean docker-down ## Clean everything including Docker volumes
	cd docker && docker compose down -v
