#!/usr/bin/env bash
# Icarus v2 (Daedalus) — W11 circuit-breaker dry-run.
#
# Drives each of the 6 capital-protecting risk breakers (drawdown,
# position_loss, tvl, gas_spike, oracle, tx_failure) end-to-end with
# synthetic state. For the 3 v2-envelope breakers, validates emitted
# ExecutionOrder envelopes against the pydantic contract on both Base
# and Solana. For the 3 gate-style breakers, asserts trip semantics.
#
# Exit 0 = all PASS; exit 1 = at least one FAIL.
#
# Usage:  bash harness/breaker_dryrun.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "harness/breaker_dryrun.sh: uv not installed; cannot run driver" >&2
    exit 1
fi

# Capture driver output so we can both stream it and pull a per-breaker
# pass/fail tally. structlog renders JSON one-event-per-line, so a simple
# grep on `breaker_check_pass` / `breaker_check_fail` is enough.
OUT_FILE="$(mktemp -t icarus-breaker-dryrun-XXXXXX.log)"
trap 'rm -f "$OUT_FILE"' EXIT INT TERM

uv run python harness/breaker_dryrun.py 2>&1 | tee "$OUT_FILE"
exit_code="${PIPESTATUS[0]}"

pass_count=$(grep -c '"event": "breaker_check_pass"' "$OUT_FILE" || true)
fail_count=$(grep -c '"event": "breaker_check_fail"' "$OUT_FILE" || true)

echo
echo "harness/breaker_dryrun.sh: ${pass_count} pass, ${fail_count} fail"

if [ "$exit_code" -eq 0 ]; then
    echo "harness/breaker_dryrun.sh: PASS"
    exit 0
fi

echo "harness/breaker_dryrun.sh: FAIL" >&2
exit "$exit_code"
