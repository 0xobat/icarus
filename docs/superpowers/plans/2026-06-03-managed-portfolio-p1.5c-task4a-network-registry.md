# Managed Portfolio P1.5c Task 4A — Network-Aware Token Registry

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox steps.

**Goal:** Make the token registry network-aware (keyed by EVM `chain_id`) so the same code resolves Base **mainnet** (8453) and Base **Sepolia** (84532) addresses, with an env-override hook. Thread `chain_id` through the resolver, holdings provider, cycle config, and `ManagedConfig`. **All defaults = mainnet (8453)**, so every existing test and behavior is unchanged.

**Architecture:** Modify `order_resolver.py` (registry by chain_id + `register_token` + `chain_id` params), `holdings.py` (thread chain_id), `managed_cycle.py` (`ManagedCycleConfig.chain_id` → pass to resolver), `config.py` (`ManagedConfig.chain_id` from env `CHAIN_ID`). Purely additive — defaulted.

**Tech Stack:** Python 3.13, `uv`, `pytest`, `Decimal`.

---

## Task 1: Network-aware registry in `order_resolver.py`

**Files:** Modify `decision-engine/src/decision_engine/order_resolver.py`; Test `decision-engine/tests/test_order_resolver_unit.py`.

- [ ] **Step 1: Failing tests** — append to the test file:

```python
from decision_engine.order_resolver import DEFAULT_CHAIN_ID, register_token

BASE_SEPOLIA = 84532


def test_default_chain_id_is_base_mainnet() -> None:
    assert DEFAULT_CHAIN_ID == 8453


def test_lookup_token_sepolia_usdc_differs_from_mainnet() -> None:
    mainnet = lookup_token("base", "USDC")  # default 8453
    sepolia = lookup_token("base", "USDC", chain_id=BASE_SEPOLIA)
    assert sepolia.address != mainnet.address
    assert sepolia.decimals == 6


def test_lookup_token_weth_same_on_both_networks() -> None:
    # WETH is the OP-stack predeploy — identical address on Base mainnet + Sepolia.
    assert (
        lookup_token("base", "WETH").address
        == lookup_token("base", "WETH", chain_id=BASE_SEPOLIA).address
        == "0x4200000000000000000000000000000000000006"
    )


def test_lookup_token_unknown_chain_id_raises() -> None:
    with pytest.raises(KeyError, match="999999"):
        lookup_token("base", "USDC", chain_id=999999)


def test_register_token_overrides_address() -> None:
    custom = "0xabc0000000000000000000000000000000000001"
    register_token(chain_id=BASE_SEPOLIA, symbol="USDC", address=custom, decimals=6)
    assert lookup_token("base", "USDC", chain_id=BASE_SEPOLIA).address == custom
    # restore so test order independence holds
    register_token(
        chain_id=BASE_SEPOLIA, symbol="USDC",
        address="0x036CbD53842c5426634e7929541eC2318f3dCF7e", decimals=6,
    )


def test_resolve_swap_params_threads_chain_id() -> None:
    params = resolve_swap_params(
        chain="base", token_in_symbol="USDC", token_out_symbol="WETH",
        usd_amount=Decimal("6000"), price_in_usd=Decimal("1"),
        price_out_usd=Decimal("3000"), recipient=_SAFE, slippage_bps=50,
        deadline_unix=1_900_000_000, chain_id=BASE_SEPOLIA,
    )
    # token_in is Sepolia USDC, not mainnet USDC.
    assert params.token_in == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
```

- [ ] **Step 2: Run → fails** (ImportError on `DEFAULT_CHAIN_ID`/`register_token`).

- [ ] **Step 3: Implement** — in `order_resolver.py`, replace the `_TOKEN_REGISTRY` block and `lookup_token` with:

```python
DEFAULT_CHAIN_ID = 8453  # Base mainnet

# Per-network token addresses, keyed by EVM chain_id. The logical `Chain`
# ("base") does not distinguish mainnet from testnet — chain_id does. WETH is
# the OP-stack predeploy (same address on every OP chain); only USDC differs.
# Operators can override any entry at boot via `register_token` (env-driven).
_TOKENS_BY_CHAIN_ID: dict[int, dict[str, TokenInfo]] = {
    8453: {  # Base mainnet
        "USDC": TokenInfo("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        "WETH": TokenInfo("0x4200000000000000000000000000000000000006", 18),
    },
    84532: {  # Base Sepolia — VERIFY before live use; override via env if stale.
        "USDC": TokenInfo("0x036CbD53842c5426634e7929541eC2318f3dCF7e", 6),
        "WETH": TokenInfo("0x4200000000000000000000000000000000000006", 18),
    },
}


def register_token(*, chain_id: int, symbol: str, address: str, decimals: int) -> None:
    """Add or override a token entry at boot (e.g. from an env-supplied address).

    Lets an operator point a network's symbol at a specific contract without a
    code change — `__main__` calls this for any address overrides in env.
    """
    _TOKENS_BY_CHAIN_ID.setdefault(chain_id, {})[symbol] = TokenInfo(address, decimals)


def lookup_token(chain: Chain, symbol: str, *, chain_id: int = DEFAULT_CHAIN_ID) -> TokenInfo:
    """Resolve (chain, symbol) → TokenInfo for the given EVM network (chain_id).

    `chain` gates the logical chain (only "base" has a registry today); `chain_id`
    selects the network's address set. Raises KeyError if unregistered.
    """
    if chain != "base":
        raise KeyError(f"no token registry for chain {chain!r}")
    try:
        network = _TOKENS_BY_CHAIN_ID[chain_id]
    except KeyError as exc:
        raise KeyError(f"unsupported chain_id {chain_id} for chain {chain!r}") from exc
    try:
        return network[symbol]
    except KeyError as exc:
        raise KeyError(f"token {symbol!r} not registered on chain_id {chain_id}") from exc
```

And thread `chain_id` into `resolve_swap_params` — add `chain_id: int = DEFAULT_CHAIN_ID` to its keyword args and pass it to BOTH `lookup_token` calls:

```python
    token_in = lookup_token(chain, token_in_symbol, chain_id=chain_id)
    token_out = lookup_token(chain, token_out_symbol, chain_id=chain_id)
```

Add `DEFAULT_CHAIN_ID` and `register_token` to `__all__`.

> Note: the existing `test_lookup_unsupported_chain_raises` (`lookup_token("solana", "USDC")`) still passes — the `chain != "base"` guard raises `KeyError` matching "solana". All existing resolve tests omit `chain_id` → default 8453 → mainnet addresses → unchanged.

- [ ] **Step 4: Run → all pass** (existing + ~6 new). **Step 5: Commit** `feat(daedalus): P1.5c-4A network-aware token registry (chain_id + override)`.

---

## Task 2: Thread `chain_id` through holdings, cycle, and config

**Files:** Modify `holdings.py`, `managed_cycle.py`, `config.py`; Tests: the existing unit files (add targeted assertions).

- [ ] **Step 1: Failing tests** —
  - In `test_holdings_unit.py`: a test that constructs `RpcHoldingsProvider(..., chain_id=84532)` and, with a mock w3 keyed by the **Sepolia** USDC address, reads balances correctly (proves it looks up Sepolia addresses).
  - In `test_managed_cycle_unit.py`: a test that sets `ManagedCycleConfig(..., chain_id=84532)` and asserts the published order's `params.token_in`/`token_out` are the **Sepolia** addresses.
  - In `test_config_unit.py`: assert `load_managed_config(..., env={...,"CHAIN_ID":"84532"}).chain_id == 84532` and that default (no `CHAIN_ID`) is `8453`.

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement:**
  - `holdings.py`: add `chain_id: int = 8453` to `RpcHoldingsProvider.__init__`; store it; pass `chain_id=self._chain_id` to `lookup_token` in `_balance_tokens`.
  - `managed_cycle.py`: add `chain_id: int = 8453` to `ManagedCycleConfig`; in `_build_order`, pass `chain_id=self.config.chain_id` to `resolve_swap_params`.
  - `config.py`: add `chain_id: int` to `ManagedConfig`; in `load_managed_config`, read `int(env.get("CHAIN_ID", "8453"))`; set it on the dataclass; `cycle_config()` sets `chain_id=self.chain_id`. (The `RpcHoldingsProvider` chain_id is wired in `__main__` from `config.chain_id` in Task 4C.)

- [ ] **Step 4: Run → all pass.** **Step 5: Commit** `feat(daedalus): P1.5c-4A thread chain_id through holdings/cycle/config`.

---

## Self-Review
- Network selection is explicit (`chain_id`), not global except the boot-time `register_token` override hook (a registration pattern, documented).
- All defaults = 8453 → every prior test/behaviour unchanged (mainnet).
- WETH-same / USDC-differs encoded and tested.
- Sepolia USDC is flagged "VERIFY / override via env" — `register_token` is the override path.

## Handoff
Subagent-Driven. After 4A: 4B (DB trade-sink) then 4C (`__main__` switchover).
