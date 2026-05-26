#!/usr/bin/env bash
# Icarus v2 (Daedalus) — W9 end-to-end smoke test.
#
# Drives the full Research → Curation → Execution path with in-repo fixtures:
# synthesised top-K backtest rows + walk-forward windows → real
# CandidateStateMachine.enter_paper_trade → seeded paper-trade observations
# → real PromotionGate.scan_eligible → real DecisionCycle.run_one with
# fakeredis as the executor publisher. Asserts orders publish on both
# execution:orders:base and execution:orders:solana.
#
# Exit 0 = pass; exit 1 = any assertion failed (the driver prints the
# offending detail via structlog JSON before exiting).
#
# Usage:  bash harness/e2e_smoke.sh

set -uo pipefail

# Resolve repo root from this script's directory so the harness works from
# any pwd (verify.sh invokes it from the repo root; an operator may call
# it directly from harness/).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Per-run scratch dir for the SQLite file. Cleaned up on exit regardless of
# the driver's outcome — keeping it would leak state across runs and make
# repeated invocations non-deterministic.
TMPDIR_SMOKE="$(mktemp -d -t icarus-smoke-XXXXXX)"
DB_PATH="$TMPDIR_SMOKE/smoke.db"
DB_URL="sqlite:///$DB_PATH"

cleanup() {
    rm -rf "$TMPDIR_SMOKE"
}
trap cleanup EXIT INT TERM

if ! command -v uv >/dev/null 2>&1; then
    echo "harness/e2e_smoke.sh: uv not installed; cannot run driver" >&2
    exit 1
fi

# Run the driver under `uv run` so the workspace's pinned interpreter +
# editable installs (lib, decision-engine, lake-governor) are on PYTHONPATH
# without us having to manage env vars by hand.
uv run python harness/e2e_smoke.py --db-url "$DB_URL"
exit_code=$?

if [ "$exit_code" -eq 0 ]; then
    echo "harness/e2e_smoke.sh: PASS"
else
    echo "harness/e2e_smoke.sh: FAIL (driver exit=$exit_code)" >&2
fi
exit "$exit_code"
