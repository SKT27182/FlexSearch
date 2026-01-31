# FlexSearch

A high-performance, local-first RAG (Retrieval-Augmented Generation) platform with project-centric knowledge management.

## Features

- **Project-based Organization**: Group documents and conversations by project
- **Modular RAG Engine**: Configurable strategies for ingestion, chunking, retrieval, and reranking
- **Real-time Chat**: WebSocket-based streaming with source attribution
- **Admin Dashboard**: User management and token usage analytics
- **Self-hosted**: Runs entirely on your infrastructure

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ with uv
- Node.js 18+ with pnpm

### 1. Start Infrastructure

```bash
cd docker
docker compose up -d
```

This starts:
- PostgreSQL (port 5432)
- Redis (port 6379)
- Qdrant (port 6333)
- MinIO (port 9000, console: 9001)

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings (API keys, etc.)
```

### 3. Start Backend

```bash
cd backend
uv venv && source .venv/bin/activate
uv pip install -e .
uvicorn app.main:app --reload --port 8000
```

### 4. Start Frontend

```bash
cd frontend
pnpm install
pnpm run dev
```

Open http://localhost:3000

## RAG Configuration

Configure strategies via environment variables:

| Variable | Options | Default |
|----------|---------|---------|
| `EXTRACTION_STRATEGY` | `ocr`, `vlm` | `ocr` |
| `CHUNKING_STRATEGY` | `fixed_window`, `recursive`, `semantic`, `parent_child` | `recursive` |
| `RETRIEVAL_STRATEGY` | `dense`, `parent_child`, `hybrid` | `dense` |
| `RERANKING_STRATEGY` | `none`, `cross_encoder` | `none` |

## Project Structure

```
FlexSearch/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routers
│   │   ├── core/         # Config, security, deps
│   │   ├── db/           # PostgreSQL + Redis
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
| POST | `/api/projects` | Create project |
| POST | `/api/documents/upload/{project_id}` | Upload document |
| WS | `/api/chat/ws/{session_id}` | Chat WebSocket |
| GET | `/api/admin/stats` | System statistics (admin) |

## Tech Stack

**Backend**: FastAPI, SQLAlchemy, Redis, Qdrant, MinIO, LiteLLM  
**Frontend**: React, TypeScript, Tailwind CSS, Zustand, Vite  
**Infrastructure**: PostgreSQL, Redis, Qdrant, MinIO

## License

MIT
