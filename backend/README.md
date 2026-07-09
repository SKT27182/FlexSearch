# FlexSearch Backend

## Documentation

| Guide | Description |
|---|---|
| [Neo4j Graph RAG (end-to-end)](docs/neo4j-graph-rag/README.md) | Full pipeline: PDF upload → extraction → graph indexing → retrieval |
| [RAG module overview](app/rag/README.md) | Vector vs Graph RAG, shared ingestion, module map |

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
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`: Redis for document status pub/sub (match infra-hub; optional `REDIS_URL` override)
- `EXTRACTION_STRATEGY`, `CHUNKING_STRATEGY`, `RETRIEVAL_STRATEGY`, `RERANKING_STRATEGY`: defaults for new projects (`RagConfig.from_settings()`); per-project overrides live in `projects.rag_config`
- `*_SERVICE_NAME`, `*_DISPLAY_NAME`, `*_CONTAINER_NAME`: service metadata

Per-project RAG, async ingestion, SSE, and reindex modes are documented in the repository root [README.md](../README.md).

## Database migrations

From repository root:

```bash
make db-migrate
```

This applies Alembic revisions under `backend/alembic/versions/` (including `users.name`).
Tables are also created/updated on app startup via `init_db()`.

If the app already started successfully and the schema is current, stamp once so Alembic tracks head:

```bash
make db-stamp
```

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
