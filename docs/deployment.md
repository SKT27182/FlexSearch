# Deployment Guide

## Option 1: Docker Compose (Recommended)

### Production Docker Compose

```yaml
# docker-compose.prod.yml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: flexsearch
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: flexsearch
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:latest
    volumes:
      - qdrant_data:/qdrant/storage
    restart: unless-stopped

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    volumes:
      - minio_data:/data
    restart: unless-stopped

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    environment:
      - DATABASE_URL=postgresql+asyncpg://flexsearch:${DB_PASSWORD}@postgres:5432/flexsearch
      - REDIS_URL=redis://redis:6379
      - QDRANT_HOST=qdrant
      - MINIO_ENDPOINT=minio:9000
      - MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}
      - MINIO_SECRET_KEY=${MINIO_SECRET_KEY}
    depends_on:
      - postgres
      - redis
      - qdrant
      - minio
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    environment:
      - VITE_API_URL=/api
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - backend
      - frontend
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  qdrant_data:
  minio_data:
```

### Backend Dockerfile

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy requirements
COPY pyproject.toml .
COPY uv.lock .

# Install dependencies
RUN uv sync --frozen

# Copy app
COPY app ./app

# Run
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Frontend Dockerfile

```dockerfile
# frontend/Dockerfile
FROM node:20-alpine AS builder

WORKDIR /app

# Install pnpm
RUN npm install -g pnpm

# Install dependencies
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Build
COPY . .
RUN pnpm run build

# Serve
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

### Nginx Configuration

```nginx
# nginx.conf
server {
    listen 80;
    server_name _;

    # Frontend
    location / {
        proxy_pass http://frontend:80;
    }

    # Backend API
    location /api {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

### Deploy

```bash
# Set environment variables
export DB_PASSWORD=your_secure_password
export MINIO_ACCESS_KEY=your_access_key
export MINIO_SECRET_KEY=your_secret_key

# Build and start
docker compose -f docker-compose.prod.yml up -d --build
```

---

## Option 2: Manual Deployment

### Backend

```bash
cd backend

# Create virtual environment
uv venv && source .venv/bin/activate
uv pip install -e .

# Set environment
export DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/flexsearch
export REDIS_URL=redis://host:6379

# Run with Gunicorn
pip install gunicorn
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

### Frontend

```bash
cd frontend

pnpm install
pnpm run build

# Serve dist/ with nginx or any static file server
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL async connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `JWT_SECRET_KEY` | Yes | Secret for JWT signing |
| `QDRANT_HOST` | Yes | Qdrant server hostname |
| `MINIO_ENDPOINT` | Yes | MinIO server endpoint |
| `MINIO_ACCESS_KEY` | Yes | MinIO access key |
| `MINIO_SECRET_KEY` | Yes | MinIO secret key |
| `LLM_API_KEY` | Yes | API key for LLM provider |
| `LLM_MODEL` | No | LLM model name (default: gpt-4o-mini) |
