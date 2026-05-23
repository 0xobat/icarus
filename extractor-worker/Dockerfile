# syntax=docker/dockerfile:1.7
# extractor-worker — Research cluster paper/blog → template
# Build context: repo root.

ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY lib/pyproject.toml lib/README.md lib/
COPY extractor-worker/pyproject.toml extractor-worker/

COPY lib/src lib/src/
COPY extractor-worker/src extractor-worker/src/

FROM base AS prod
RUN uv sync --frozen --package extractor-worker --no-dev
CMD ["python", "-m", "extractor_worker"]

FROM base AS dev
RUN uv sync --frozen --package extractor-worker
CMD ["watchfiles", "--filter", "python", "python -m extractor_worker", "/app/extractor-worker/src", "/app/lib/src"]
