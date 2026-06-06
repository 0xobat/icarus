# Managed Portfolio — PnL Deposit Tracker (cost-basis from external deposits)

> **Reporting feature, NOT capital-protection.** Must be entirely best-effort and read-only: a failure here (API error, missing config) logs and continues — it can NEVER alter or halt a trading decision. Isolated from the gate and the cycle, exactly like the trade log.

**Goal:** Replace "what's my portfolio worth" guesswork with a **calculated PnL**: track **net external deposits** (operator funds in, minus withdrawals out), priced at deposit time → **contributed capital**; then **PnL = current NAV − contributed capital**, surfaced each tick for reporting. This is the operator's "I sent ETH/USDC to the wallet, what's my running balance and growth" need (2026-06-05 decision: cost-basis from deposits).

**Why not just NAV:** NAV (live from `balanceOf`) already answers "what's it worth now." This feature answers "how much did I *put in* vs what it's *worth now*" — growth attribution, which needs deposit history. Keep the two distinct.

## Key design decisions (flagged — change on review if wrong)
1. **Classification by funding address.** A *deposit* = inbound transfer to the Safe **from** a configured operator address; a *withdrawal* = outbound transfer from the Safe **to** a configured operator address. Everything else (DEX swap proceeds, the Safe's own WETH-wrap, faucets) is ignored. New env `OPERATOR_FUNDING_ADDRESSES` (comma-separated, checksummed). This cleanly excludes internal flows — the hard part of "sum all input txs." (Testnet note: Circle-faucet USDC comes from the faucet, not the operator EOA, so it's excluded unless the operator adds the faucet address; real mainnet deposits come from the operator's own EOA → counted.)
2. **Net-deposits cost basis.** `contributed_usd = Σ(deposit_amount × price@deposit_block) − Σ(withdrawal_amount × price@withdrawal_block)`. Simple "money in − money out," not FIFO/LIFO lot tracking. Sound baseline for growth %; document it's not tax-grade cost basis.
3. **Pricing at block:** USDC deposits valued at **$1** at deposit time (depeg at the exact deposit block is rare and second-order for cost basis; the live depeg breaker handles trading). ETH/WETH valued at the **Chainlink ETH/USD price at the deposit block** (new `RpcAdapter.eth_price_at_block`).
4. **Data source:** Alchemy `alchemy_getAssetTransfers` (operator already has an Alchemy key). Categories `["external","erc20"]` for inbound native ETH + ERC-20 (USDC/WETH). Use `rawContract.value` (hex smallest-units) + decimals for precision, `blockNum` (hex) for the block. Degrade gracefully if the method is unsupported on the active network (log + report contributed=None).
5. **Surfacing:** compute on a slow cadence (deposits change rarely — refresh every N ticks / cached), and log a structured `portfolio_pnl` event each tick: `nav_usd`, `contributed_usd`, `pnl_usd`, `pnl_pct`. **DB persistence + Grafana is a deliberate fast-follow (v1.1), not this change** — keep v1 to a computed, logged value.

**Tech Stack:** Python 3.13, `uv`, `pytest` (`asyncio_mode=auto`), `web3` AsyncWeb3 (`provider.make_request` for the Alchemy method), Decimal arithmetic, structlog.

---

## File Structure
- **Create** `decision-engine/src/decision_engine/pnl.py` — pure netting/pricing + a `ContributedCapitalTracker` (fetch + cache).
- **Modify** `lib/src/icarus/data_adapters/rpc.py` — add `eth_price_at_block(block_no) -> Decimal` (reuse the cached ETH feed contract + `latestRoundData(block_identifier=...)`, non-positive guard).
- **Modify** `decision-engine/src/decision_engine/config.py` — parse `OPERATOR_FUNDING_ADDRESSES` env into a frozenset of checksummed addresses (optional; empty → tracker disabled).
- **Modify** `decision-engine/src/decision_engine/__main__.py` — construct the tracker (when funding addrs + Alchemy available); `ManagedEngine` logs `portfolio_pnl` each tick (best-effort, wrapped in try/except like the trade log). Boot warning if funding addrs unset (PnL tracking disabled).
- **Modify** `.env.example` — document `OPERATOR_FUNDING_ADDRESSES`.
- **Create tests** — `decision-engine/tests/test_pnl_unit.py`; extend rpc + engine tests.

---

## Task 1: Pure netting + pricing (test-first)
**Files:** `pnl.py`; `tests/test_pnl_unit.py`.
- [ ] Model a `Transfer` (asset, amount Decimal, block_no, direction: "in"/"out", counterparty). Pure `net_contributed_usd(transfers, *, eth_price_at_block) -> Decimal`: USDC→$1, ETH/WETH→price@block; deposits add, withdrawals subtract.
- [ ] Failing tests: ETH deposit valued at block price; USDC deposit at $1; a withdrawal subtracts; mixed deposits/withdrawals net correctly; empty → 0.

## Task 2: Classification + fetch (test-first, mocked)
**Files:** `pnl.py`; tests.
- [ ] `ContributedCapitalTracker(*, w3, safe_address, funding_addresses, eth_price_at_block, usdc_address, weth_address)`:
  - `async refresh()` → calls `alchemy_getAssetTransfers` (to=Safe and from=Safe), keeps only transfers whose counterparty ∈ funding_addresses, classifies in/out, computes + caches `contributed_usd`.
  - `contributed_usd` property (None until first successful refresh).
- [ ] Tests with a mocked `make_request`: only funding-address transfers counted; swap-proceeds (from a non-funding address) ignored; in/out classified by Safe being to/from; API error → refresh logs + leaves cache unchanged (returns prior or None), never raises to the caller.

## Task 3: Adapter price-at-block (test-first)
**Files:** `rpc.py`; rpc tests.
- [ ] `eth_price_at_block(block_no)` mirrors `_eth_price_usd` but with `block_identifier`; non-positive → raise (or 0 + log, matching the historical pattern — pick consistent with existing code). Test with mocked web3.

## Task 4: Wiring (best-effort, isolated)
**Files:** `config.py`, `__main__.py`, `.env.example`, engine tests.
- [ ] `config.py`: `OPERATOR_FUNDING_ADDRESSES` → frozenset (checksummed via `Web3.to_checksum_address`); empty/unset → tracker disabled.
- [ ] `__main__`: build the tracker when funding addrs present; refresh on a slow cadence (e.g. every Nth tick or a timer); `ManagedEngine._tick` logs `portfolio_pnl` (nav, contributed, pnl, pnl_pct) — **wrapped in try/except, never affects the cycle**. Boot warning when disabled.
- [ ] `.env.example`: document `OPERATOR_FUNDING_ADDRESSES` (per [[feedback-update-env-example-on-var-change]]).
- [ ] Engine test: a stub tracker returning a fixed contributed value → `_tick` logs the right `pnl_usd`/`pnl_pct`; a tracker that raises → tick still completes and trades unaffected.

## Task 5: Verify
- [ ] `uv run ruff check decision-engine/ lib/` clean; `uv run pytest decision-engine/ lib/ -q` all pass.
- [ ] Read-through: tracker disabled (no funding addrs) → engine logs a "pnl_disabled" note and trades normally; tracker error → logged, trading unaffected; with funding addrs → `portfolio_pnl` shows nav/contributed/pnl.

## Out of scope (v1.1+)
- DB `portfolio_snapshots` table + Grafana panel (the obvious next step for "reporting").
- FIFO/LIFO lot-level cost basis; realized vs unrealized PnL split.
- Multi-asset deposits beyond ETH/WETH/USDC (arrives with SOL/wBTC in P2).
- Reorg/finality handling beyond Alchemy's confirmed transfers.
