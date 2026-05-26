#!/usr/bin/env bash
# Icarus v2 (Daedalus) — local verification gate.
#
# Progressive: each section is independent and skipped if its tooling isn't
# ready yet. Sections turn on as the build sequence progresses through W1-W12.
# `harness/verify.sh` MUST be green at the end of every commit per the
# blueprint's "Each week ends with a demoable artifact, harness/verify.sh
# green" rule.
#
# Run from repo root:  bash harness/verify.sh

set -uo pipefail

cd "$(dirname "$0")/.."

# Colors only when stdout is a tty.
if [ -t 1 ]; then
  GREEN="\033[0;32m"; RED="\033[0;31m"; YELLOW="\033[0;33m"; DIM="\033[2m"; RESET="\033[0m"
else
  GREEN=""; RED=""; YELLOW=""; DIM=""; RESET=""
fi

FAIL_COUNT=0
SKIP_COUNT=0
PASS_COUNT=0

section() {
  echo
  echo -e "${DIM}── $* ──${RESET}"
}

ok()   { echo -e "  ${GREEN}✓${RESET} $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
skip() { echo -e "  ${YELLOW}~${RESET} $* ${DIM}(skipped)${RESET}"; SKIP_COUNT=$((SKIP_COUNT + 1)); }
fail() { echo -e "  ${RED}✗${RESET} $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

run_or_fail() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    ok "$label"
  else
    fail "$label"
    echo -e "    ${DIM}command: $*${RESET}"
    "$@" 2>&1 | sed 's/^/    /' | head -20
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
section "Python workspace (uv)"

if ! command -v uv >/dev/null 2>&1; then
  fail "uv not installed — see https://docs.astral.sh/uv/getting-started/installation/"
else
  run_or_fail "uv lock is consistent" uv lock --check
  run_or_fail "uv sync --all-packages" uv sync --all-packages --quiet
  run_or_fail "ruff check (lib + 4 services)" \
    uv run ruff check lib decision-engine lake-governor extractor-worker backtest-worker
  run_or_fail "icarus.types import" \
    uv run python -c "from icarus.types import MarketSnapshot, PortfolioSnapshot, Decision"
  run_or_fail "icarus.protocols import" \
    uv run python -c "from icarus.protocols import Allocator, RegimeClassifier, DecayDetector, Extractor, BacktestEngine, DataAdapter"
  run_or_fail "service modules import" \
    uv run python -c "import decision_engine, lake_governor, extractor_worker, backtest_worker"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Python tests"
if find lib decision-engine lake-governor extractor-worker backtest-worker \
       -path '*/tests/*test_*.py' -print -quit 2>/dev/null | grep -q .; then
  run_or_fail "pytest" uv run pytest -q
else
  skip "no pytest test files yet (added as each service builds)"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Node services (pnpm)"

if ! command -v pnpm >/dev/null 2>&1; then
  fail "pnpm not installed — run 'corepack enable && corepack prepare pnpm@latest --activate'"
else
  for svc in ts-executor solana-executor webapp; do
    if [ ! -f "$svc/package.json" ]; then
      skip "$svc: no package.json yet (awaits implementation)"
      continue
    fi
    if [ ! -d "$svc/node_modules" ]; then
      skip "$svc: node_modules missing — run 'pnpm install' in $svc/"
      continue
    fi
    run_or_fail "$svc: typecheck" bash -lc "cd $svc && pnpm run typecheck"
    # Run tests only where a test script + test files exist.
    if grep -q '"test"' "$svc/package.json" && [ -d "$svc/tests" ]; then
      run_or_fail "$svc: tests" bash -lc "cd $svc && pnpm test"
    fi
  done
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Shared schemas (JSON Schema)"
if [ -d "shared/schemas" ] && ls shared/schemas/*.json >/dev/null 2>&1; then
  # v2 schemas (singular, snake_case, chain discriminator) at shared/schemas/ root.
  # ts-executor + decision-engine + solana-executor all consume these directly.
  for schema in shared/schemas/*.json; do
    [ -f "$schema" ] || continue
    run_or_fail "schemas/$(basename "$schema")" \
      uv run python -c "import json,sys; json.load(open('$schema'))"
  done
else
  skip "shared/schemas/ empty (awaits validated copy-back from .archive/)"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "End-to-end smoke (W9)"
if [ -f "harness/e2e_smoke.sh" ]; then
  run_or_fail "harness/e2e_smoke.sh" bash harness/e2e_smoke.sh
else
  skip "e2e smoke harness not yet authored (W9)"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Cluster isolation (W11)"
if [ -f "harness/verify_cluster_isolation.sh" ]; then
  # The cluster-isolation script returns exit 0 in two cases:
  #   1. PASS — invariant holds across all clusters
  #   2. SKIP — docker daemon unavailable (env can't run the test)
  # We disambiguate by inspecting stdout for "PASS" vs "skipped — docker".
  # This keeps the gate honest: a missing docker daemon must not silently
  # masquerade as a pass.
  iso_out=$(bash harness/verify_cluster_isolation.sh 2>&1)
  iso_rc=$?
  if [ $iso_rc -ne 0 ]; then
    fail "cluster-isolation invariant"
    echo "$iso_out" | sed 's/^/    /' | tail -20
  elif echo "$iso_out" | grep -qE 'skipped — docker|skipped -- docker'; then
    skip "cluster-isolation (docker daemon unavailable)"
  else
    ok "cluster-isolation invariant"
  fi
else
  skip "cluster-isolation test not yet authored (W11)"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Breaker dry-run (W11)"
if [ -f "harness/breaker_dryrun.sh" ]; then
  run_or_fail "harness/breaker_dryrun.sh (6 breakers, both chains)" \
    bash harness/breaker_dryrun.sh
else
  skip "breaker dry-run harness not yet authored (W11)"
fi

# ──────────────────────────────────────────────────────────────────────────────
section "Grafana dashboards"
if [ -f "monitoring/grafana/validate.sh" ]; then
  if ! command -v jq >/dev/null 2>&1; then
    skip "jq not installed — install via 'brew install jq' or apt-get install jq"
  else
    run_or_fail "grafana dashboard JSON valid" bash monitoring/grafana/validate.sh
  fi
else
  skip "monitoring/grafana/validate.sh not present"
fi

# ──────────────────────────────────────────────────────────────────────────────
echo
echo -e "${DIM}─────────────────────────────────${RESET}"
echo -e "  ${GREEN}${PASS_COUNT} pass${RESET}   ${YELLOW}${SKIP_COUNT} skip${RESET}   ${RED}${FAIL_COUNT} fail${RESET}"
echo -e "${DIM}─────────────────────────────────${RESET}"

if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
