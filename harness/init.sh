#!/usr/bin/env bash
# icarus init: dual-service setup (TypeScript + Python)
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "icarus: checking prerequisites..."

# Check Docker & Docker Compose
if ! command -v docker &>/dev/null; then
  echo "ERROR: docker not installed"
  exit 1
fi
echo "✓ docker found"

if ! docker compose version &>/dev/null 2>&1; then
  echo "ERROR: docker compose not available"
  exit 1
fi
echo "✓ docker compose found"

# Check pnpm for TypeScript service
if ! command -v pnpm &>/dev/null; then
  echo "ERROR: pnpm not installed. Install: npm install -g pnpm"
  exit 1
fi
echo "✓ pnpm found"

# Check uv for Python service
if ! command -v uv &>/dev/null; then
  echo "ERROR: uv not installed. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
echo "✓ uv found"

# TypeScript service
echo ""
echo "icarus: installing TypeScript dependencies..."
if [ -f "$PROJECT_ROOT/ts-executor/package.json" ]; then
  (cd "$PROJECT_ROOT/ts-executor" && pnpm install --frozen-lockfile 2>/dev/null || pnpm install)
  echo "✓ ts-executor dependencies installed"
else
  echo "WARNING: ts-executor/package.json not found, skipping"
fi

# Python service
echo ""
echo "icarus: installing Python dependencies..."
if [ -f "$PROJECT_ROOT/py-engine/pyproject.toml" ]; then
  (cd "$PROJECT_ROOT/py-engine" && uv sync --extra dev)
  echo "✓ py-engine dependencies installed"
else
  echo "WARNING: py-engine/pyproject.toml not found, skipping"
fi

# Environment file
echo ""
if [ ! -f "$PROJECT_ROOT/.env" ]; then
  if [ -f "$PROJECT_ROOT/.env.example" ]; then
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    echo "✓ .env created from .env.example (configure before running)"
  else
    echo "WARNING: .env.example not found"
  fi
else
  echo "✓ .env already exists"
fi

echo ""
echo "✓ icarus initialized"
