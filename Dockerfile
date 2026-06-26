# ╔══════════════════════════════════════════════════════════════════════╗
# ║  CIVITAS-RAG — Dockerfile multi-stage                               ║
# ║  Stage base → deps → production                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝

ARG PYTHON_VERSION=3.11
ARG BUILD_DATE=""
ARG GIT_COMMIT=""

# ── Stage 1: base ─────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gcc \
        g++ \
        libpq-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r civitas && useradd -r -g civitas -d /app -s /bin/false civitas

WORKDIR /app

# ── Stage 2: deps ─────────────────────────────────────────────────────
FROM base AS deps

COPY pyproject.toml .
COPY civitas/__init__.py civitas/

# Installer les dépendances dans un virtualenv isolé
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip setuptools wheel && \
    pip install \
        qdrant-client \
        sentence-transformers \
        scikit-learn \
        rich \
        pyyaml \
        python-dotenv \
        chardet \
        pypdf \
        python-docx \
        psycopg2-binary \
        sqlalchemy \
        alembic \
        httpx \
        pydantic \
        pydantic-settings

# ── Stage 3: production ───────────────────────────────────────────────
FROM base AS production

LABEL org.opencontainers.image.title="CIVITAS-RAG" \
      org.opencontainers.image.description="CIVITAS — Système d'ingestion vectorielle Qdrant" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.source="https://github.com/amourgit/CIVITAS-RAG"

# Copier le venv depuis l'étape deps
COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copier le code source
COPY --chown=civitas:civitas civitas/   ./civitas/
COPY --chown=civitas:civitas scripts/   ./scripts/
COPY --chown=civitas:civitas config/    ./config/

# Créer les répertoires nécessaires
RUN mkdir -p /app/data /app/logs /app/.civitas_tracker && \
    chown -R civitas:civitas /app

USER civitas

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "scripts/qdrant_ingest.py", "--help"]
