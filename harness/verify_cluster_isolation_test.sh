#!/usr/bin/env bash
# Unit-level tests for verify_cluster_isolation.sh.
#
# Tests the parser logic (service_state, assert_all_running, assert_all_stopped)
# without invoking docker, by sourcing the script with a sentinel and overriding
# `docker` as a shell function that emits canned JSON.
#
# Run: bash harness/verify_cluster_isolation_test.sh
# Exit 0 = all tests pass.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/verify_cluster_isolation.sh"

if [ ! -f "$TARGET" ]; then
  echo "FAIL: $TARGET not found"
  exit 1
fi

# We can't `source` the script (it runs top-level logic). Instead, we extract
# the parser helpers and re-evaluate them in this shell.
HELPERS=$(awk '
  /^service_state\(\) \{/,/^}$/ { print; next }
  /^assert_all_running\(\) \{/,/^}$/ { print; next }
  /^assert_all_stopped\(\) \{/,/^}$/ { print; next }
' "$TARGET")

# Provide minimal color shims expected by the helpers.
GREEN=""; RED=""; YELLOW=""; DIM=""; RESET=""
info() { :; }   # silence info lines
fail() { echo "  FAIL: $*" >&2; }

# shellcheck disable=SC1090
eval "$HELPERS"

TESTS_PASSED=0
TESTS_FAILED=0

t_pass() { TESTS_PASSED=$((TESTS_PASSED + 1)); echo "  ok: $1"; }
t_fail() { TESTS_FAILED=$((TESTS_FAILED + 1)); echo "  FAIL: $1"; }

# ── Test 1: service_state parses "running" ──────────────────────────────────
docker() {
  # Mimic: docker compose ps --all --format json <svc>
  echo '{"Name":"icarus-extractor-worker-1","Service":"extractor-worker","State":"running","Status":"Up 5 seconds"}'
}
state=$(service_state extractor-worker)
if [ "$state" = "running" ]; then
  t_pass "service_state parses State:running"
else
  t_fail "service_state expected 'running', got '$state'"
fi

# ── Test 2: service_state parses "exited" ───────────────────────────────────
docker() {
  echo '{"Name":"icarus-webapp-1","Service":"webapp","State":"exited","Status":"Exited (0)"}'
}
state=$(service_state webapp)
if [ "$state" = "exited" ]; then
  t_pass "service_state parses State:exited"
else
  t_fail "service_state expected 'exited', got '$state'"
fi

# ── Test 3: service_state handles missing service (empty output) ────────────
docker() { :; }   # emit nothing
state=$(service_state nonexistent)
if [ "$state" = "missing" ]; then
  t_pass "service_state handles missing service"
else
  t_fail "service_state expected 'missing', got '$state'"
fi

# ── Test 4: assert_all_running passes when all are running ──────────────────
docker() {
  # The last positional arg is the service name in the real script's call.
  local svc="${!#}"
  echo "{\"Name\":\"x\",\"Service\":\"$svc\",\"State\":\"running\",\"Status\":\"Up\"}"
}
if assert_all_running "test" postgres redis >/dev/null 2>&1; then
  t_pass "assert_all_running passes when all running"
else
  t_fail "assert_all_running should have passed"
fi

# ── Test 5: assert_all_running fails when one is exited ─────────────────────
docker() {
  local svc="${!#}"
  if [ "$svc" = "redis" ]; then
    echo "{\"Name\":\"x\",\"Service\":\"$svc\",\"State\":\"exited\",\"Status\":\"Exited\"}"
  else
    echo "{\"Name\":\"x\",\"Service\":\"$svc\",\"State\":\"running\",\"Status\":\"Up\"}"
  fi
}
if assert_all_running "test" postgres redis >/dev/null 2>&1; then
  t_fail "assert_all_running should have failed when redis exited"
else
  t_pass "assert_all_running fails when one service exited"
fi

# ── Test 6: assert_all_stopped passes when none running ─────────────────────
docker() {
  local svc="${!#}"
  echo "{\"Name\":\"x\",\"Service\":\"$svc\",\"State\":\"exited\",\"Status\":\"Exited\"}"
}
if assert_all_stopped "test" extractor-worker backtest-worker >/dev/null 2>&1; then
  t_pass "assert_all_stopped passes when all stopped"
else
  t_fail "assert_all_stopped should have passed"
fi

# ── Test 7: assert_all_stopped fails when one still running ─────────────────
docker() {
  local svc="${!#}"
  if [ "$svc" = "extractor-worker" ]; then
    echo "{\"Name\":\"x\",\"Service\":\"$svc\",\"State\":\"running\",\"Status\":\"Up\"}"
  else
    echo "{\"Name\":\"x\",\"Service\":\"$svc\",\"State\":\"exited\",\"Status\":\"Exited\"}"
  fi
}
if assert_all_stopped "test" extractor-worker backtest-worker >/dev/null 2>&1; then
  t_fail "assert_all_stopped should have failed (extractor-worker still running)"
else
  t_pass "assert_all_stopped fails when service still running"
fi

# ── Report ──────────────────────────────────────────────────────────────────
echo
echo "─────────────────────────────────"
echo "  $TESTS_PASSED pass   $TESTS_FAILED fail"
echo "─────────────────────────────────"

if [ $TESTS_FAILED -gt 0 ]; then
  exit 1
fi
exit 0
