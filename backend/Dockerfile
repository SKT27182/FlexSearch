# =============================================================================
# FlexSearch Backend - Dockerfile
# =============================================================================
FROM python:3.11-slim

# System dependencies for OCR/PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no venv needed inside container)
RUN uv pip install --system -e .

# Copy application code
COPY . .

ENV API_PORT=8889

EXPOSE 8889

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT}"]
