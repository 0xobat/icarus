#!/usr/bin/env bash
# Icarus v2 (Daedalus) — W11 Discord round-trip + signing dry-run.
#
# Operator-runnable script that exercises the full APPROVE → promotion →
# ExecutionOrder → sign-but-don't-broadcast path on both chains. Phase 1
# is pure Python (no keys); Phases 2 and 3 require operator-supplied
# wallet credentials and are auto-SKIPped when those env vars are absent.
#
# Exit 0 = every phase PASS or SKIP; exit 1 = any phase actually FAILED.
#
# NOT wired into harness/verify.sh on purpose — this needs real operator
# keys, not a CI gate. Run by hand:
#
#     bash harness/signing_dryrun.sh
#
# Env contract:
#   WALLET_PRIVATE_KEY            — runs Phase 2 (Safe / Base) when set.
#   SOLANA_MEMBER_KEYPAIR_PATH    — runs Phase 3 (Squads / Solana) when set.
#                                   SOLANA_MULTISIG_PDA is forced unset by
#                                   the driver to keep the W7 single-signer
#                                   fallback path active.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Per-run scratch dir for the SQLite Phase 1 file. Cleaned up on exit
# regardless of driver outcome so re-runs don't leak state.
TMPDIR_SIGN="$(mktemp -d -t icarus-signing-dryrun-XXXXXX)"
DB_PATH="$TMPDIR_SIGN/signing.db"
DB_URL="sqlite:///$DB_PATH"

cleanup() {
    rm -rf "$TMPDIR_SIGN"
}
trap cleanup EXIT INT TERM

if ! command -v uv >/dev/null 2>&1; then
    echo "harness/signing_dryrun.sh: uv not installed; cannot run driver" >&2
    exit 1
fi

# Resolve per-phase skip flags from env. The driver duplicates these
# checks internally, but surfacing them here gives the operator a clean
# pre-run summary they can read before any work happens.
PHASE2_FLAG=""
PHASE3_FLAG=""

if [ -z "${WALLET_PRIVATE_KEY:-}" ]; then
    echo "harness/signing_dryrun.sh: WALLET_PRIVATE_KEY unset → Phase 2 (Safe) will SKIP"
    PHASE2_FLAG="--skip-phase2"
else
    echo "harness/signing_dryrun.sh: WALLET_PRIVATE_KEY present → Phase 2 (Safe) will run"
fi

if [ -z "${SOLANA_MEMBER_KEYPAIR_PATH:-}" ]; then
    echo "harness/signing_dryrun.sh: SOLANA_MEMBER_KEYPAIR_PATH unset → Phase 3 (Squads) will SKIP"
    PHASE3_FLAG="--skip-phase3"
else
    echo "harness/signing_dryrun.sh: SOLANA_MEMBER_KEYPAIR_PATH present → Phase 3 (Squads) will run"
fi

OUT_FILE="$(mktemp -t icarus-signing-dryrun-out-XXXXXX.log)"
trap 'rm -rf "$TMPDIR_SIGN" "$OUT_FILE"' EXIT INT TERM

# shellcheck disable=SC2086
uv run python harness/signing_dryrun.py \
    --db-url "$DB_URL" \
    $PHASE2_FLAG $PHASE3_FLAG \
    2>&1 | tee "$OUT_FILE"
exit_code="${PIPESTATUS[0]}"

# Pull the single summary event we emit at the end.
summary_line=$(grep '"event": "signing_dryrun.summary"' "$OUT_FILE" | tail -1)

echo
if [ -n "$summary_line" ]; then
    echo "harness/signing_dryrun.sh: summary → $summary_line"
fi

if [ "$exit_code" -eq 0 ]; then
    echo "harness/signing_dryrun.sh: PASS"
    exit 0
fi

echo "harness/signing_dryrun.sh: FAIL (driver exit=$exit_code)" >&2
exit "$exit_code"
