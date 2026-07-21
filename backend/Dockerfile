# =============================================================================
# FlexSearch Backend - Dockerfile
# =============================================================================
FROM python:3.11.13-slim-bookworm

# System dependencies for OCR/PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.8.19 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for caching
COPY pyproject.toml uv.lock ./

# Install exactly the locked production dependencies without the local project.
RUN uv sync --frozen --no-dev --no-install-project

# Copy only runtime files; secrets and development artifacts never enter a layer.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN addgroup --system flexsearch && adduser --system --ingroup flexsearch flexsearch \
    && mkdir -p /tmp/flexsearch && chown -R flexsearch:flexsearch /app /tmp/flexsearch

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER flexsearch

ENV API_PORT=8889

EXPOSE 8889

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT}"]
