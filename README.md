# FlexSearch

A high-performance, local-first retrieval-first RAG platform with project-centric knowledge management.

## Features

- **Project-based Organization**: Group documents and retrieval workflows by project
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

Make sure the required services (PostgreSQL, Qdrant, MinIO) are already running and reachable on the ports configured in `backend/.env`.

### 2. Configure Environment

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
# Edit backend/.env and frontend/.env with your settings
```

### 3. Start Backend

```bash
cd backend
uv venv && source .venv/bin/activate
uv pip install -e .
source .env
uvicorn app.main:app --reload --port "${API_PORT}"
```

### 4. Start Frontend

```bash
cd frontend
pnpm install
pnpm run dev
```

Open http://localhost:5144

### Direct deploy (no Docker)

```bash
make deploy-local
```

### Docker + Nginx deployment flow

- Run app containers with Docker Compose (frontend on `127.0.0.1:5144`, backend on `127.0.0.1:8889`).
- Keep these ports local-only (not publicly exposed).
- Configure your host Nginx to reverse proxy domain traffic to these localhost ports.

## RAG Configuration

Configure strategies via environment variables:

| Variable | Options | Default |
|----------|---------|---------|
| `EXTRACTION_STRATEGY` | `ocr`, `vlm` | `ocr` |
| `CHUNKING_STRATEGY` | `fixed_window`, `recursive`, `semantic`, `parent_child` | `fixed_window` |
| `RETRIEVAL_STRATEGY` | `dense`, `parent_child`, `hybrid` | `dense` |
| `RERANKING_STRATEGY` | `none`, `cross_encoder` | `none` |

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
| POST | `/api/projects` | Create project |
| POST | `/api/projects/{project_id}/documents/upload` | Upload document |
| POST | `/api/retrieval/query` | Retrieve relevant chunks for query |
| GET | `/api/admin/stats` | System statistics (admin) |

## Tech Stack

**Backend**: FastAPI, SQLAlchemy, Qdrant, MinIO, LiteLLM  
**Frontend**: React, TypeScript, Tailwind CSS, Zustand, Vite  
**Infrastructure**: PostgreSQL, Qdrant, MinIO

## License

MIT
