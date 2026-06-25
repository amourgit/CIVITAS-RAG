# CIVITAS API — Production Dockerfile
FROM python:3.11-slim AS base

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Application
COPY civitas/ ./civitas/
COPY config/ ./config/

# Non-root user
RUN useradd -m -r civitas && chown -R civitas:civitas /app
USER civitas

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "civitas.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
