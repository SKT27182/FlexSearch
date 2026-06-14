# FlexSearch

A high-performance, local-first retrieval-first RAG platform with project-centric knowledge management.

## Features

- **Project-based Organization**: Group documents and retrieval workflows by project
- **Dual RAG modes**: Choose **Vector RAG** (Qdrant) or **Graph RAG** (Neo4j) per project at creation — modes are mutually exclusive
- **Modular RAG Engine**: Configurable strategies for ingestion, chunking, retrieval, and reranking
- **Retrieval API**: Stateless query endpoint returning ranked chunks and metadata
- **Admin Dashboard**: User and document management
- **Self-hosted**: Runs entirely on your infrastructure

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ with uv
- Node.js 18+ with pnpm
- **System Dependencies (for OCR/PDFs)**:
  - Tesseract OCR (`tesseract-ocr`)
  - Poppler Utils (`poppler-utils`)

#### Installation (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install -y tesseract-ocr poppler-utils
```

#### Installation (macOS)
```bash
brew install tesseract poppler
```

### 1. Start Infrastructure

Make sure the required services are running and reachable on the ports configured in `backend/.env`:

- **Vector RAG projects**: PostgreSQL, Qdrant, MinIO, Redis
- **Graph RAG projects**: PostgreSQL, **Neo4j** (from [infra-hub](https://github.com)), MinIO, Redis, plus `API_KEY` for LLM entity extraction

### 2. Configure Environment

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
# Edit backend/.env and frontend/.env with your settings
```

### 3. Install dependencies

```bash
make install
```

On first run, backend startup ensures the configured PostgreSQL database exists and creates required tables if missing.

### 4. Run local dev (no Docker)

```bash
make dev-local
```

Open http://localhost:5144

### Run with Docker

```bash
make dev
```

### Docker + Nginx deployment flow

- Run app containers with Docker Compose (frontend on `127.0.0.1:5144`, backend on `127.0.0.1:8889`).
- Keep these ports local-only (not publicly exposed).
- Configure your host Nginx to reverse proxy domain traffic to these localhost ports.

## Per-project RAG configuration

Each project has an immutable **`rag_mode`** chosen at creation:

| Mode | Storage | Retrieval strategies |
|------|---------|----------------------|
| `vector` | Qdrant chunk embeddings | `dense`, `bm25`, `hybrid`, `parent_child` |
| `graph` | Neo4j knowledge graph | `graph_local`, `graph_global` |

Graph projects use LLM-based entity/relation extraction during indexing. Set `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` in `backend/.env` to match infra-hub Neo4j credentials.

Each project stores a `rag_config` JSON object (shape depends on mode). Set it when creating a project in the UI or via `POST /api/projects`. Per-query overrides are supported on `POST /api/retrieval/query` without changing stored project settings.

**Defaults:** New projects inherit strategy *names* from environment variables (see table below). `RagConfig.from_settings()` in the backend builds the full default object including strategy-specific params. After create, the project’s stored `rag_config` is the source of truth for ingestion and default retrieval.

| Strategy | Options | Env default |
|----------|---------|-------------|
| Extraction | `ocr`, `vlm` | `EXTRACTION_STRATEGY` → `ocr` |
| Chunking | `fixed_window`, `recursive`, `semantic`, `parent_child` | `CHUNKING_STRATEGY` → `fixed_window` |
| Retrieval | `dense`, `bm25`, `hybrid`, `parent_child` | `RETRIEVAL_STRATEGY` → `dense` |
| Reranking | `none`, `cross_encoder` | `RERANKING_STRATEGY` → `none` |

`GET /api/rag/options` lists allowed strategy values for the UI.

### Retrieval strategies

| Strategy | Type | When to use |
|----------|------|-------------|
| `dense` | Semantic (embedding similarity in Qdrant) | Paraphrases, conceptual queries |
| `bm25` | Lexical (BM25 keyword ranking only) | Exact terms, IDs, rare tokens |
| `hybrid` | Semantic + BM25 fused with RRF | General-purpose default for mixed queries |
| `parent_child` | Search child chunks, return parent context | Only with **parent_child** chunking and a full reindex |

`bm25` and `hybrid` build an in-memory BM25 index from project chunks on first query (same chunk source as Qdrant). `hybrid` also runs dense vector search and merges ranks.

## Document ingestion pipeline

Uploads return **201** immediately after the raw file is stored in MinIO; processing runs in a FastAPI background task.

| Status | Meaning |
|--------|---------|
| `uploaded` | Record created |
| `stored` | Raw object in MinIO |
| `extracting` | Text extraction in progress |
| `extracted` | Text saved; chunking may follow |
| `chunking` | Chunking in progress |
| `indexing` | Vectors written to Qdrant |
| `completed` | Ready for retrieval |
| `failed` | See `error_message` |

Artifacts under `{project_id}/{document_id}/` in MinIO:

- `raw.{ext}` — uploaded file
- `extracted.md` — normalized text for preview and reindex
- `extracted.meta.json` — includes `content_format` (`plain` or `markdown`)

`extraction_config_hash` on the document row skips re-extraction on reindex when extraction settings are unchanged and `extracted.md` still exists.

Preview extracted text: `GET /api/projects/{project_id}/documents/{document_id}/content` (available from `extracted` onward).

## Redis and real-time status

Set `REDIS_HOST`, `REDIS_PORT`, and `REDIS_PASSWORD` in `backend/.env` (same values as infra-hub `backend/.env`). After each document status commit, the worker publishes JSON to:

- `flexsearch:document:{document_id}`
- `flexsearch:project:{project_id}`

Postgres remains the source of truth; Redis is notify-only.

## SSE (upload progress)

The UI subscribes via `@microsoft/fetch-event-source` with the JWT `Authorization` header (browser `EventSource` cannot send Bearer tokens).

| Endpoint | Scope |
|----------|--------|
| `GET /api/projects/{project_id}/documents/events` | All documents in project |
| `GET /api/projects/{project_id}/documents/{document_id}/events` | Single document |

Events:

- `snapshot` — current row from DB on connect
- `status` — forwarded Redis payload (`status`, `processing_step`, `progress_pct`, …)

Stream ends when the document reaches `completed` or `failed`.

**Nginx:** For SSE through a reverse proxy, disable buffering, e.g. `proxy_buffering off;` and `X-Accel-Buffering: no` on the location. Vite dev proxy should not buffer long-lived responses.

If Redis or SSE is unavailable, the UI can fall back to polling `GET .../documents`.

## Reindex

`POST /api/projects/{project_id}/reindex?mode=auto|full|from_extracted`

| Mode | Behavior |
|------|----------|
| `auto` (default) | Re-extract only if `extraction_config_hash` changed or `extracted.md` is missing; otherwise chunk + index only |
| `full` | Always re-extract, then chunk and index |
| `from_extracted` | Require existing `extracted.md`; chunk and index only |

Run reindex after changing **chunking** or **extraction** in project settings. The project settings panel offers reindex actions after saving RAG config.

## Project Structure

```
FlexSearch/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routers
│   │   ├── core/         # Config, security, deps
│   │   ├── db/           # PostgreSQL
│   │   ├── rag/          # RAG pipeline + strategies
│   │   ├── services/     # Storage, Vector, LLM
│   │   └── schemas/      # Pydantic models
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/   # UI components
│       ├── pages/        # Route pages
│       ├── stores/       # Zustand state
│       └── lib/          # API client, utils
└── docker/
    └── docker-compose.yml
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register (first user = admin) |
| POST | `/api/auth/login` | Login, get JWT tokens |
| GET | `/api/projects` | List user's projects |
| POST | `/api/projects` | Create project (`rag_config` optional) |
| PATCH | `/api/projects/{project_id}` | Update project (`rag_config`) |
| POST | `/api/projects/{project_id}/reindex` | Reprocess documents (`mode` query param) |
| POST | `/api/projects/{project_id}/documents/upload` | Upload document (async processing) |
| GET | `/api/projects/{project_id}/documents` | List documents |
| GET | `/api/projects/{project_id}/documents/events` | SSE: project document statuses |
| GET | `/api/projects/{project_id}/documents/{id}/events` | SSE: single document |
| GET | `/api/projects/{project_id}/documents/{id}/content` | Extracted text preview |
| POST | `/api/projects/{project_id}/documents/{id}/retry` | Re-run ingestion for stuck/failed document |
| POST | `/api/retrieval/query` | Retrieve chunks (`top_k`, `overrides` optional) |
| GET | `/api/rag/options` | Allowed RAG strategy values (optional `?rag_mode=vector\|graph`) |
| PATCH | `/api/projects/{id}/rag-mode` | Destructive switch between vector and graph RAG |
| GET | `/api/projects/{id}/graph-index/status` | Graph index status (graph projects) |
| POST | `/api/projects/{id}/graph-index/rebuild` | Trigger graph reindex |
| GET | `/api/projects/{id}/graph-export` | Download parquet + GraphML zip for visualization |
| GET | `/api/admin/stats` | System statistics (admin) |

## Graph RAG (Microsoft GraphRAG)

FlexSearch supports two **exclusive** project modes at creation time:

| Mode | Index | Retrieval strategies |
|------|-------|---------------------|
| **vector** (default) | Qdrant chunk embeddings | `dense`, `bm25`, `hybrid`, `parent_child` |
| **graph** | Microsoft GraphRAG (Parquet + LanceDB in MinIO) | `graph_local`, `graph_global` |

### Worktree development

Implement Graph RAG work on branch `feature/graph_rag` in a **separate git worktree** — do not switch branches in your main FlexSearch checkout:

```bash
cd /path/to/FlexSearch   # main repo — any branch is fine
git worktree add ../FlexSearch-feature-graph-rag feature/graph_rag
cd ../FlexSearch-feature-graph-rag
```

### Graph storage

- **Structure**: GraphRAG parquet tables (`entities`, `relationships`, `communities`, …) and optional **GraphML** snapshot under `projects/{id}/graphrag/` in MinIO
- **Embeddings**: LanceDB inside the GraphRAG workspace (synced to MinIO with `output/`)
- **Qdrant**: not used for pure graph projects

### LLM requirements

Graph indexing and graph search use the same `MODEL_NAME` and `API_KEY` as VLM extraction. Set `GRAPH_INDEXING_ENABLED=false` in dev to skip graph builds globally.

### Visualization

When the graph index is **ready**, download **Graph export** from the project page and open in:

- [GraphRAG Visualizer](https://noworneverev.github.io/graphrag-visualizer/) (upload parquet)
- [Gephi](https://gephi.org) via GraphML (see [GraphRAG visualization guide](https://microsoft.github.io/graphrag/guides/visualization))

### Mode switch

`PATCH /api/projects/{id}/rag-mode` wipes the old index (Qdrant or graph workspace) and requeues all documents.

## Tech Stack

**Backend**: FastAPI, SQLAlchemy, Alembic, Qdrant, MinIO, Redis (pub/sub), LiteLLM, Microsoft GraphRAG  
**Frontend**: React, TypeScript, Tailwind CSS, Zustand, Vite, `@microsoft/fetch-event-source`  
**Infrastructure**: PostgreSQL, Qdrant, MinIO, Redis

## License

MIT
