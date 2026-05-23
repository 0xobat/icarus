# syntax=docker/dockerfile:1.7
# lake-governor — Curation cluster paper-trade + state machine
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
COPY lake-governor/pyproject.toml lake-governor/

COPY lib/src lib/src/
COPY lake-governor/src lake-governor/src/

FROM base AS prod
RUN uv sync --frozen --package lake-governor --no-dev
CMD ["python", "-m", "lake_governor"]

FROM base AS dev
RUN uv sync --frozen --package lake-governor
CMD ["watchfiles", "--filter", "python", "python -m lake_governor", "/app/lake-governor/src", "/app/lib/src"]
