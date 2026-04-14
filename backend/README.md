# FlexSearch Backend

This backend is fully env-driven and reads runtime/deploy values from `backend/.env` via `app/core/config.py`.
On startup, it also ensures the configured PostgreSQL database exists and creates tables if missing.

## Key ports for FlexSearch

- **Frontend (Vite dev / Nginx exposed):** `5144`
- **Backend API:** `8889` (`API_PORT`)

## Environment setup

```bash
cp .env.example .env
```

Important variables:

- `API_PORT`: FastAPI bind port (default `8889`)
- `SERVICE_PUBLIC_HOST`: public host used for generated service/admin links
- `CORS_ORIGINS`: comma-separated or JSON array of allowed origins
- `POSTGRES_*`, `QDRANT_*`, `MINIO_*`: service connection values
- `*_SERVICE_NAME`, `*_DISPLAY_NAME`, `*_CONTAINER_NAME`: service metadata

## Local run

From repository root:

```bash
make dev-backend
```

For direct non-Docker deployment of both backend and frontend:

```bash
make deploy-local
```

Or manually:

```bash
cd backend
source .env
uv venv && source .venv/bin/activate
uv pip install -e .
uvicorn app.main:app --reload --port "${API_PORT}"
```

## Networking model

- Backend always serves API under `/api`.
- Frontend uses relative `/api` requests.
- In local dev, Vite proxies `/api` to `VITE_DEV_API_TARGET`.
- With Docker Compose, frontend maps to `127.0.0.1:5144` and backend to `127.0.0.1:8889` (localhost-only).
- In production, Nginx reverse proxies domain traffic to these localhost ports.
