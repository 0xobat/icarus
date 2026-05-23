# syntax=docker/dockerfile:1.7
# decision-engine — Execution cluster runtime cycle
# Build context: repo root (so the uv workspace is accessible).
# Build: docker build -f decision-engine/Dockerfile --target {dev|prod} .

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

# Workspace metadata first (cache-friendly)
COPY pyproject.toml uv.lock README.md ./
COPY lib/pyproject.toml lib/README.md lib/
COPY decision-engine/pyproject.toml decision-engine/

# Lib source (decision-engine depends on it)
COPY lib/src lib/src/
COPY decision-engine/src decision-engine/src/

FROM base AS prod
RUN uv sync --frozen --package decision-engine --no-dev
CMD ["python", "-m", "decision_engine"]

FROM base AS dev
RUN uv sync --frozen --package decision-engine
# watchfiles auto-restarts on source changes; mount lib/ and decision-engine/ as volumes in compose
CMD ["watchfiles", "--filter", "python", "python -m decision_engine", "/app/decision-engine/src", "/app/lib/src"]
