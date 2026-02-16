FROM python:3.12-slim AS base
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Install dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Copy source
COPY . .

# ── Development target (auto-reload via watchfiles) ──
FROM base AS dev
RUN uv sync --frozen
CMD ["uv", "run", "--", "python", "-u", "main.py"]

# ── Production target ──
FROM base AS prod
CMD ["uv", "run", "--", "python", "-u", "main.py"]
