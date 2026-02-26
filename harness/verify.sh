#!/usr/bin/env bash
# icarus verification: checks both TS and Python services
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ERRORS=0

echo "icarus: verifying project structure..."

# Check critical directories
for dir in ts-executor py-engine shared/schemas; do
  if [ ! -d "$PROJECT_ROOT/$dir" ]; then
    echo "ERROR: Required directory missing: $dir"
    ERRORS=$((ERRORS + 1))
  fi
done

# ── TypeScript service ──────────────────────────────────────
echo ""
echo "── ts-executor ──"

if [ ! -f "$PROJECT_ROOT/ts-executor/package.json" ]; then
  echo "ERROR: ts-executor/package.json missing"
  ERRORS=$((ERRORS + 1))
else
  pushd "$PROJECT_ROOT/ts-executor" > /dev/null

  # Type checking
  if [ -f "tsconfig.json" ] && [ -d "src" ]; then
    echo "Running TypeScript type check..."
    if ! pnpm exec tsc --noEmit; then
      echo "ERROR: TypeScript type check failed"
      ERRORS=$((ERRORS + 1))
    fi
  fi

  # Linting
  echo "Running ESLint..."
  if ! pnpm lint; then
    echo "ERROR: ESLint check failed"
    ERRORS=$((ERRORS + 1))
  fi

  # Tests
  if [ -d "tests" ] && ls tests/*.test.ts &>/dev/null 2>&1; then
    echo "Running TS tests..."
    if ! pnpm test; then
      echo "ERROR: TS tests failed"
      ERRORS=$((ERRORS + 1))
    fi
  else
    echo "INFO: No TS tests found yet"
  fi

  popd > /dev/null
fi

# ── Python service ──────────────────────────────────────────
echo ""
echo "── py-engine ──"

if [ ! -f "$PROJECT_ROOT/py-engine/pyproject.toml" ]; then
  echo "ERROR: py-engine/pyproject.toml missing"
  ERRORS=$((ERRORS + 1))
else
  pushd "$PROJECT_ROOT/py-engine" > /dev/null

  # Syntax check on all Python files
  echo "Running Python syntax check..."
  SYNTAX_OK=true
  while IFS= read -r pyfile; do
    if ! uv run python -m py_compile "$pyfile" 2>/dev/null; then
      echo "ERROR: Syntax error in $pyfile"
      SYNTAX_OK=false
    fi
  done < <(find . -name "*.py" -not -path "./.venv/*")
  if [ "$SYNTAX_OK" = false ]; then
    ERRORS=$((ERRORS + 1))
  fi

  # Linting
  echo "Running ruff check..."
  if ! uv run ruff check .; then
    echo "ERROR: ruff check failed"
    ERRORS=$((ERRORS + 1))
  fi

  # Tests
  if [ -d "tests" ] && ls tests/test_*.py &>/dev/null 2>&1; then
    echo "Running pytest..."
    if ! uv run pytest tests/ --tb=short -q; then
      echo "ERROR: pytest failed"
      ERRORS=$((ERRORS + 1))
    fi
  else
    echo "INFO: No Python tests found yet"
  fi

  popd > /dev/null
fi

# ── Shared schemas ──────────────────────────────────────────
echo ""
echo "── shared/schemas ──"

if ls "$PROJECT_ROOT/shared/schemas/"*.schema.json &>/dev/null 2>&1; then
  echo "Validating JSON schemas..."
  for schema in "$PROJECT_ROOT/shared/schemas/"*.schema.json; do
    if ! python3 -c "import json; json.load(open('$schema'))" 2>/dev/null; then
      echo "ERROR: Invalid JSON in $schema"
      ERRORS=$((ERRORS + 1))
    fi
  done
  echo "✓ All schemas are valid JSON"
else
  echo "WARNING: No JSON schemas found in shared/schemas/"
fi

# ── Docker Compose ──────────────────────────────────────────
echo ""
if [ -f "$PROJECT_ROOT/docker-compose.yml" ]; then
  echo "Validating docker-compose.yml..."
  if docker compose -f "$PROJECT_ROOT/docker-compose.yml" config -q 2>/dev/null; then
    echo "✓ docker-compose.yml valid"
  else
    echo "WARNING: docker-compose.yml validation failed (Dockerfiles may not exist yet)"
  fi
fi

# ── Results ─────────────────────────────────────────────────
echo ""
if [ $ERRORS -gt 0 ]; then
  echo "icarus: FAILED ($ERRORS errors)"
  exit 1
fi

echo "✓ icarus: all checks passed"
exit 0
