#!/usr/bin/env bash
# Icarus v2 (Daedalus) — W11 cluster-isolation invariant test.
#
# Blueprint invariant (docs/blueprint.md ~L224 + L502):
#   "Stop any one cluster, the others keep running."
#
# Decomposition (see docker-compose.yml):
#   Research cluster:  extractor-worker, backtest-worker
#   Curation cluster:  lake-governor, webapp                (grafana is a sidecar,
#                                                            not the cluster's core)
#   Execution cluster: decision-engine, ts-executor, solana-executor, inference
#   Shared infra:      postgres, redis
#
# Per-cluster test (3 phases):
#   1. Stop the cluster's services (`docker compose stop <services>`).
#   2. Verify every service in the OTHER two clusters is still in `running` state.
#   3. Restart the stopped cluster; verify it reaches running state again.
#
# Exit codes:
#   0 — invariant holds, OR docker daemon unavailable (skip)
#   1 — some cluster failure cascaded across a boundary
#
# Flags:
#   --quick     Run only the Research cluster's isolation test (development feedback).
#   --no-up     Assume the stack is already up (skip the `compose up -d --wait`).
#   --no-down   Leave the stack running on exit (default: `compose down`).
#
# Idempotent: tears down on entry (unless --no-up) and exit (unless --no-down).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── Cluster decomposition ────────────────────────────────────────────────────
RESEARCH_SERVICES=(extractor-worker backtest-worker)
CURATION_SERVICES=(lake-governor webapp)
EXECUTION_SERVICES=(decision-engine ts-executor solana-executor inference)
INFRA_SERVICES=(postgres redis)

# ── Flags ────────────────────────────────────────────────────────────────────
QUICK=0
NO_UP=0
NO_DOWN=0
for arg in "$@"; do
  case "$arg" in
    --quick)   QUICK=1 ;;
    --no-up)   NO_UP=1 ;;
    --no-down) NO_DOWN=1 ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *)
      echo "unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

# ── Colors (tty only) ────────────────────────────────────────────────────────
if [ -t 1 ]; then
  GREEN="\033[0;32m"; RED="\033[0;31m"; YELLOW="\033[0;33m"; DIM="\033[2m"; RESET="\033[0m"
else
  GREEN=""; RED=""; YELLOW=""; DIM=""; RESET=""
fi

log()  { echo -e "$*"; }
info() { echo -e "${DIM}  $*${RESET}"; }
pass() { echo -e "  ${GREEN}✓${RESET} $*"; }
fail() { echo -e "  ${RED}✗${RESET} $*"; }

# ── Docker availability check (FAST — must skip within 5s if no daemon) ─────
# Use a hard 5-second timeout via `timeout` if available; otherwise rely on
# docker's own short connect timeout. `docker compose ps` against a missing
# daemon fails almost immediately, so this stays well under budget.
docker_available() {
  if ! command -v docker >/dev/null 2>&1; then
    return 1
  fi
  # `docker compose version` doesn't talk to the daemon, but we need to verify
  # the daemon itself responds. `docker info` does, and exits fast on failure.
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 docker info >/dev/null 2>&1
  else
    docker info >/dev/null 2>&1
  fi
}

if ! docker_available; then
  log "${YELLOW}~ cluster-isolation: skipped — docker compose unavailable${RESET}"
  log "${DIM}  (docker daemon not running or docker not installed)${RESET}"
  exit 0
fi

# ── Helper: docker compose ps for a service, returns its state ───────────────
# `docker compose ps --format json` emits one JSON object per line per service.
# We avoid jq (not a hard dep) and grep for the State field on the matching
# service line. Returns "running", "exited", "missing", etc. on stdout.
service_state() {
  local svc="$1"
  # `--all` includes stopped containers so we can distinguish "stopped" from
  # "never created".
  local line
  line=$(docker compose ps --all --format json "$svc" 2>/dev/null | head -1)
  if [ -z "$line" ]; then
    echo "missing"
    return
  fi
  # Parse "State":"running" out of the JSON without jq.
  local state
  state=$(echo "$line" | sed -n 's/.*"State":"\([^"]*\)".*/\1/p')
  if [ -z "$state" ]; then
    echo "unknown"
  else
    echo "$state"
  fi
}

# ── Helper: assert every service in a list is `running` ──────────────────────
# Returns 0 if all running; 1 otherwise (with details printed).
assert_all_running() {
  local label="$1"; shift
  local services=("$@")
  local all_ok=1
  for svc in "${services[@]}"; do
    local state
    state=$(service_state "$svc")
    if [ "$state" = "running" ]; then
      info "$label: $svc → $state"
    else
      fail "$label: $svc → $state (expected: running)"
      all_ok=0
    fi
  done
  [ "$all_ok" = 1 ]
}

# ── Helper: assert services in list are NOT running (we stopped them) ────────
assert_all_stopped() {
  local label="$1"; shift
  local services=("$@")
  local all_ok=1
  for svc in "${services[@]}"; do
    local state
    state=$(service_state "$svc")
    if [ "$state" = "running" ]; then
      fail "$label: $svc → $state (expected: stopped/exited)"
      all_ok=0
    else
      info "$label: $svc → $state"
    fi
  done
  [ "$all_ok" = 1 ]
}

# ── Per-cluster test ─────────────────────────────────────────────────────────
# Args: <cluster-name> <stopped-services-array-name> <other-cluster-A> <other-cluster-B>
# The three array names are resolved via bash indirect expansion.
test_cluster_isolation() {
  local cluster="$1"
  local -n stopped_ref="$2"
  local -n other_a_ref="$3"
  local -n other_b_ref="$4"

  log ""
  log "${DIM}── isolation test: $cluster cluster ──${RESET}"

  # Phase 1: stop this cluster's services.
  log "Phase 1: stopping $cluster cluster services (${stopped_ref[*]})..."
  if ! docker compose stop "${stopped_ref[@]}" >/dev/null 2>&1; then
    fail "$cluster: docker compose stop failed"
    return 1
  fi
  if ! assert_all_stopped "$cluster (stopped)" "${stopped_ref[@]}"; then
    fail "$cluster: services did not stop"
    return 1
  fi
  pass "$cluster: services stopped cleanly"

  # Phase 2: verify other clusters + infra still running.
  log "Phase 2: verifying other clusters + infra are unaffected..."
  local phase2_ok=1
  if ! assert_all_running "other-cluster-A" "${other_a_ref[@]}"; then
    phase2_ok=0
  fi
  if ! assert_all_running "other-cluster-B" "${other_b_ref[@]}"; then
    phase2_ok=0
  fi
  if ! assert_all_running "infra" "${INFRA_SERVICES[@]}"; then
    phase2_ok=0
  fi
  if [ "$phase2_ok" = 0 ]; then
    fail "$cluster: ISOLATION VIOLATED — failure cascaded across cluster boundary"
    # Best-effort restart before we bail.
    docker compose start "${stopped_ref[@]}" >/dev/null 2>&1 || true
    return 1
  fi
  pass "$cluster: other clusters + infra still healthy (isolation holds)"

  # Phase 3: restart this cluster, verify recovery.
  log "Phase 3: restarting $cluster cluster..."
  if ! docker compose start "${stopped_ref[@]}" >/dev/null 2>&1; then
    fail "$cluster: docker compose start failed"
    return 1
  fi
  # Give services a moment to flip to running. Health is a stronger check than
  # "container exists" but most app services here don't define a healthcheck;
  # `running` is the strongest signal docker compose gives us uniformly.
  local attempts=0
  local restart_ok=0
  while [ $attempts -lt 10 ]; do
    if assert_all_running "$cluster (restarted)" "${stopped_ref[@]}" >/dev/null 2>&1; then
      restart_ok=1
      break
    fi
    sleep 1
    attempts=$((attempts + 1))
  done
  if [ "$restart_ok" = 0 ]; then
    assert_all_running "$cluster (restarted)" "${stopped_ref[@]}"
    fail "$cluster: services did not return to running within 10s"
    return 1
  fi
  pass "$cluster: services restarted and healthy"

  return 0
}

# ── Bring stack up (clean state) ─────────────────────────────────────────────
cleanup() {
  if [ "$NO_DOWN" = 0 ]; then
    log ""
    log "${DIM}cleanup: docker compose down...${RESET}"
    docker compose down --remove-orphans >/dev/null 2>&1 || true
  fi
}

if [ "$NO_UP" = 0 ]; then
  log "${DIM}preflight: docker compose down (clean state)...${RESET}"
  docker compose down --remove-orphans >/dev/null 2>&1 || true

  log "${DIM}bringing stack up (docker compose up -d --wait, may take ~30s)...${RESET}"
  if ! docker compose up -d --wait >/dev/null 2>&1; then
    fail "docker compose up --wait failed (Dockerfiles or build context broken?)"
    log "${DIM}retrying without --wait to capture state...${RESET}"
    docker compose up -d 2>&1 | tail -20
    cleanup
    exit 1
  fi
  pass "stack up"
else
  log "${DIM}--no-up: assuming stack is already running${RESET}"
fi

trap cleanup EXIT

# ── Run cluster tests ────────────────────────────────────────────────────────
OVERALL_PASS=1

if ! test_cluster_isolation "Research" RESEARCH_SERVICES CURATION_SERVICES EXECUTION_SERVICES; then
  OVERALL_PASS=0
fi

if [ "$QUICK" = 1 ]; then
  log ""
  log "${YELLOW}--quick: skipping Curation + Execution cluster tests${RESET}"
else
  if ! test_cluster_isolation "Curation" CURATION_SERVICES RESEARCH_SERVICES EXECUTION_SERVICES; then
    OVERALL_PASS=0
  fi
  if ! test_cluster_isolation "Execution" EXECUTION_SERVICES RESEARCH_SERVICES CURATION_SERVICES; then
    OVERALL_PASS=0
  fi
fi

# ── Report ───────────────────────────────────────────────────────────────────
log ""
log "${DIM}─────────────────────────────────${RESET}"
if [ "$OVERALL_PASS" = 1 ]; then
  log "  ${GREEN}cluster-isolation: PASS${RESET}"
  log "${DIM}─────────────────────────────────${RESET}"
  exit 0
else
  log "  ${RED}cluster-isolation: FAIL${RESET}"
  log "${DIM}─────────────────────────────────${RESET}"
  exit 1
fi
