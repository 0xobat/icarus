# Price Feed Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace CoinGecko with Alchemy Token Prices API as primary price source, keep DefiLlama as fallback, add configurable fetch interval to avoid unnecessary API calls.

**Architecture:** Primary+fallback pattern — try Alchemy first, fall back to DefiLlama on failure. Cache-freshness check at top of `fetch_prices()` skips API calls entirely when prices are younger than `PRICE_FETCH_INTERVAL_SECONDS` (default 30). Removes dual-source cross-check logic (overkill for stablecoins). All 4 tokens (USDC, USDT, DAI, AERO) fetched from the same source in a single API call.

**Tech Stack:** Python 3.12, urllib.request (stdlib), Redis (via RedisManager), pytest

**Design doc:** `docs/plans/2026-03-07-price-feed-redesign-design.md`

---

### Task 1: Add `_fetch_alchemy()` method and update constants

**Files:**
- Modify: `py-engine/data/price_feed.py:16-36` (constants) and add new method
- Test: `py-engine/tests/test_price_feed.py`

**Step 1: Write the failing test for `_fetch_alchemy()`**

Add to `py-engine/tests/test_price_feed.py` — a new test class after imports:

```python
# Replace the import block at line 11-17 with:
from data.price_feed import (
    DEFILLAMA_TOKEN_ADDRESSES,
    PRICE_CACHE_KEY_PREFIX,
    ALCHEMY_SYMBOLS,
    PriceFeedManager,
    PriceResult,
)


def _make_alchemy_response(prices: dict[str, float]) -> dict[str, Any]:
    """Build a mock Alchemy Token Prices API response."""
    return {
        "data": [
            {
                "symbol": symbol,
                "prices": [{"currency": "usd", "value": str(price), "lastUpdatedAt": "2026-01-01T00:00:00Z"}],
            }
            for symbol, price in prices.items()
        ]
    }


class TestAlchemyFetch:
    def test_fetches_all_tokens(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            assert "api.g.alchemy.com/prices" in url
            return _make_alchemy_response({"USDC": 1.0001, "USDT": 1.0, "DAI": 0.9998, "AERO": 1.25})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr._fetch_alchemy()

        assert len(result) == 4
        assert result["USDC"].price_usd == pytest.approx(1.0001)
        assert result["USDC"].source == "alchemy"
        assert result["AERO"].price_usd == pytest.approx(1.25)

    def test_handles_missing_token_gracefully(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_alchemy_response({"USDC": 1.0})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr._fetch_alchemy()

        assert "USDC" in result
        assert "USDT" not in result

    def test_no_api_key_raises(self) -> None:
        redis = _make_mock_redis()
        mgr = PriceFeedManager(redis)
        with pytest.raises(ValueError, match="ALCHEMY_API_KEY"):
            mgr._fetch_alchemy()
```

**Step 2: Run tests to verify they fail**

Run: `cd py-engine && uv run pytest tests/test_price_feed.py::TestAlchemyFetch -v`
Expected: FAIL — `ALCHEMY_SYMBOLS` import fails, `alchemy_api_key` param doesn't exist

**Step 3: Implement the changes in `price_feed.py`**

Replace the constants block (lines 15-36) with:

```python
# Tokens tracked by Alchemy (symbol used directly in API call)
ALCHEMY_SYMBOLS: list[str] = ["USDC", "USDT", "DAI", "AERO"]

# DeFi Llama fallback addresses
DEFILLAMA_TOKEN_ADDRESSES: dict[str, str] = {
    "USDC": "coingecko:usd-coin",
    "USDT": "coingecko:tether",
    "DAI": "coingecko:dai",
    "AERO": "coingecko:aerodrome-finance",
}

# L2-specific token metadata (kept for get_l2_tokens / is_l2_token helpers)
L2_TOKEN_MAPPINGS: dict[str, dict[str, str]] = {
    "AERO": {
        "chain": "base",
        "contract": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    },
}
```

Update `__init__` to accept `alchemy_api_key`:

```python
def __init__(
    self,
    redis: RedisManager,
    *,
    ttl_seconds: int = DEFAULT_PRICE_TTL_SECONDS,
    deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
    tokens: dict[str, str] | None = None,
    fetch_fn: Any = None,
    alchemy_api_key: str | None = None,
    fetch_interval_seconds: int | None = None,
) -> None:
    self._redis = redis
    self._ttl = ttl_seconds
    self._deviation_threshold = deviation_threshold
    self._tokens = tokens  # legacy, kept for backward compat if passed
    self._fetch_fn = fetch_fn or _fetch_url
    self._alchemy_api_key = alchemy_api_key or os.environ.get("ALCHEMY_API_KEY") or os.environ.get("ALCHEMY_SEPOLIA_API_KEY")
    self._fetch_interval = fetch_interval_seconds or int(os.environ.get("PRICE_FETCH_INTERVAL_SECONDS", "30"))
    self._last_fetch_time: float = 0.0
```

Add `import os` at the top of the file (after `import time`).

Add the `_fetch_alchemy()` method:

```python
def _fetch_alchemy(self) -> dict[str, PriceResult]:
    """Fetch prices from Alchemy Token Prices API."""
    if not self._alchemy_api_key:
        raise ValueError("ALCHEMY_API_KEY is required for Alchemy price fetches")

    symbols = "&".join(f"symbols={s}" for s in ALCHEMY_SYMBOLS)
    url = f"https://api.g.alchemy.com/prices/v1/{self._alchemy_api_key}/tokens/by-symbol?{symbols}"
    now = datetime.now(UTC).isoformat()

    data = self._fetch_fn(url)
    results: dict[str, PriceResult] = {}

    for entry in data.get("data", []):
        symbol = entry.get("symbol", "").upper()
        prices = entry.get("prices", [])
        if symbol in ALCHEMY_SYMBOLS and prices:
            usd_price = next(
                (p for p in prices if p.get("currency") == "usd"), None
            )
            if usd_price:
                results[symbol] = PriceResult(
                    token=symbol,
                    price_usd=float(usd_price["value"]),
                    source="alchemy",
                    timestamp=now,
                )

    return results
```

**Step 4: Run tests to verify they pass**

Run: `cd py-engine && uv run pytest tests/test_price_feed.py::TestAlchemyFetch -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add py-engine/data/price_feed.py py-engine/tests/test_price_feed.py
git commit -m "feat(icarus): add Alchemy Token Prices API fetcher + update token constants"
```

---

### Task 2: Rewrite `fetch_prices()` with cache-freshness + primary/fallback

**Files:**
- Modify: `py-engine/data/price_feed.py:303-369` (`fetch_prices()`)
- Test: `py-engine/tests/test_price_feed.py`

**Step 1: Write failing tests for new `fetch_prices()` flow**

Replace `TestMultiSourceAggregation` class (lines 135-194) with:

```python
class TestFetchPricesFlow:
    def test_alchemy_success_caches_and_returns(self) -> None:
        """When Alchemy succeeds, prices are cached and returned."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                return _make_alchemy_response({"USDC": 1.0001, "USDT": 1.0})
            raise ConnectionError("Should not call fallback")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        assert result["USDC"]["price_usd"] == pytest.approx(1.0001)
        assert result["USDC"]["sources"] == ["alchemy"]
        assert redis.cache_set.call_count >= 2

    def test_alchemy_fail_falls_back_to_defillama(self) -> None:
        """When Alchemy fails, DefiLlama is tried."""
        redis = _make_mock_redis()
        call_log: list[str] = []

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "api.g.alchemy.com" in url:
                call_log.append("alchemy")
                raise ConnectionError("Alchemy down")
            if "coins.llama.fi" in url:
                call_log.append("defillama")
                return _make_defillama_response({"USDC": 1.0, "DAI": 0.999})
            raise ConnectionError("Unknown URL")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "alchemy" in call_log
        assert "defillama" in call_log
        assert "USDC" in result
        assert result["USDC"]["sources"] == ["defillama"]

    def test_both_fail_returns_empty(self) -> None:
        """When both sources fail, return empty dict."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            raise ConnectionError("Network down")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert result == {}

    def test_cache_freshness_skips_api_calls(self) -> None:
        """When all prices are fresh (within fetch_interval), skip API calls."""
        redis = _make_mock_redis()
        api_called = False

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            nonlocal api_called
            api_called = True
            return _make_alchemy_response({"USDC": 1.0})

        mgr = PriceFeedManager(
            redis, fetch_fn=mock_fetch, alchemy_api_key="test-key",
            fetch_interval_seconds=30,
        )

        # First call — should hit API
        mgr.fetch_prices()
        assert api_called

        # Second call — should return cached (within 30s)
        api_called = False
        result = mgr.fetch_prices()
        assert not api_called
        assert "USDC" in result

    def test_no_alchemy_key_tries_defillama_directly(self) -> None:
        """When no Alchemy key is set, skip Alchemy and try DefiLlama."""
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            if "coins.llama.fi" in url:
                return _make_defillama_response({"USDC": 1.0})
            raise ConnectionError("Should not call Alchemy")

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key=None)
        # Ensure env var doesn't leak in
        import os
        old = os.environ.pop("ALCHEMY_API_KEY", None)
        old2 = os.environ.pop("ALCHEMY_SEPOLIA_API_KEY", None)
        try:
            mgr._alchemy_api_key = None
            result = mgr.fetch_prices()
            assert "USDC" in result
        finally:
            if old:
                os.environ["ALCHEMY_API_KEY"] = old
            if old2:
                os.environ["ALCHEMY_SEPOLIA_API_KEY"] = old2
```

**Step 2: Run tests to verify they fail**

Run: `cd py-engine && uv run pytest tests/test_price_feed.py::TestFetchPricesFlow -v`
Expected: FAIL — old `fetch_prices()` still uses CoinGecko dual-source logic

**Step 3: Rewrite `fetch_prices()` in `price_feed.py`**

Replace the entire `fetch_prices()` method (lines 303-369) with:

```python
def fetch_prices(self) -> dict[str, dict[str, Any]]:
    """Fetch prices: Alchemy primary, DefiLlama fallback, with cache-freshness check.

    Returns a dict of token -> {price_usd, timestamp, sources}.
    Skips API calls if prices were fetched within fetch_interval_seconds.
    """
    now_epoch = time.time()

    # Cache-freshness check — if we fetched recently, return cached prices
    if now_epoch - self._last_fetch_time < self._fetch_interval:
        cached = self._get_all_cached_prices()
        if cached:
            return cached

    results: dict[str, dict[str, Any]] = {}
    source_results: dict[str, PriceResult] = {}

    # Primary: Alchemy
    if self._alchemy_api_key:
        try:
            source_results = self._fetch_alchemy()
        except Exception as e:
            _log("price_source_error", f"Alchemy fetch failed: {e}", source="alchemy")

    # Fallback: DefiLlama (only if Alchemy returned nothing)
    if not source_results:
        try:
            source_results = self._fetch_defillama()
        except Exception as e:
            _log("price_source_error", f"DefiLlama fetch failed: {e}", source="defillama")

    # Cache and return whatever we got
    for token, pr in source_results.items():
        self._cache_price(token, pr.price_usd, pr.timestamp)
        self._record_price_history(token, pr.price_usd, now_epoch)
        results[token] = {
            "price_usd": pr.price_usd,
            "timestamp": pr.timestamp,
            "sources": [pr.source],
        }

    if results:
        self._last_fetch_time = now_epoch

    return results

def _get_all_cached_prices(self) -> dict[str, dict[str, Any]]:
    """Return all cached prices for known tokens."""
    results: dict[str, dict[str, Any]] = {}
    for symbol in ALCHEMY_SYMBOLS:
        cached = self.get_cached_price(symbol)
        if cached and not cached.get("stale", False):
            results[symbol] = {
                "price_usd": cached["price_usd"],
                "timestamp": cached["timestamp"],
                "sources": ["cached"],
            }
    return results
```

**Step 4: Run tests to verify they pass**

Run: `cd py-engine && uv run pytest tests/test_price_feed.py::TestFetchPricesFlow -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add py-engine/data/price_feed.py py-engine/tests/test_price_feed.py
git commit -m "feat(icarus): rewrite fetch_prices() with Alchemy primary + DefiLlama fallback"
```

---

### Task 3: Remove CoinGecko code and update AERO in DefiLlama

**Files:**
- Modify: `py-engine/data/price_feed.py` (remove `_fetch_coingecko`, `fetch_l2_prices`, `_check_deviation`, old `SUPPORTED_TOKENS`)
- Test: `py-engine/tests/test_price_feed.py` (remove `TestOracleManipulationGuard`, `_make_coingecko_response`, update `TestPriceResult`)

**Step 1: Remove dead code from `price_feed.py`**

Delete these methods entirely:
- `_fetch_coingecko()` (lines 175-194)
- `fetch_l2_prices()` (lines 133-171)
- `_check_deviation()` (lines 220-241)

Remove the old `SUPPORTED_TOKENS` constant (already replaced in Task 1).

Remove the `tokens` parameter from `__init__` and `self._tokens`.
Remove `self._deviation_threshold` from `__init__`.

Remove the `DEFAULT_DEVIATION_THRESHOLD` constant (line 40).

**Step 2: Remove dead test code from `test_price_feed.py`**

- Remove `_make_coingecko_response()` helper (lines 71-79)
- Remove `SUPPORTED_TOKENS` from imports
- Remove `TestOracleManipulationGuard` class (lines 104-132)
- Remove `TestCustomTokenList` class (lines 328-342)
- Update `TestPriceResult.test_to_dict` — change source from `"coingecko"` to `"alchemy"`:

```python
class TestPriceResult:
    def test_to_dict(self) -> None:
        pr = PriceResult("USDC", 1.0, "alchemy", "2026-01-01T00:00:00+00:00")
        d = pr.to_dict()
        assert d["token"] == "USDC"
        assert d["price_usd"] == 1.0
        assert d["source"] == "alchemy"
```

**Step 3: Update `TestTimestampsUTC` to use Alchemy mock**

Replace the class (lines 310-325) with:

```python
class TestTimestampsUTC:
    def test_prices_have_utc_timestamps(self) -> None:
        redis = _make_mock_redis()

        def mock_fetch(url: str, timeout: int = 10) -> Any:
            return _make_alchemy_response({"USDC": 1.000})

        mgr = PriceFeedManager(redis, fetch_fn=mock_fetch, alchemy_api_key="test-key")
        result = mgr.fetch_prices()

        assert "USDC" in result
        ts = result["USDC"]["timestamp"]
        assert "+00:00" in ts
```

**Step 4: Run all price feed tests**

Run: `cd py-engine && uv run pytest tests/test_price_feed.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add py-engine/data/price_feed.py py-engine/tests/test_price_feed.py
git commit -m "refactor(icarus): remove CoinGecko code, deviation guard, and L2 price fetcher"
```

---

### Task 4: Update L2 data pipeline tests

**Files:**
- Modify: `py-engine/tests/test_l2_data_pipeline.py:80-92`

**Step 1: Update `TestL2TokenMappings` to not require `coingecko_id`**

The `L2_TOKEN_MAPPINGS` no longer has `coingecko_id` (it was CoinGecko-specific). Update:

```python
class TestL2TokenMappings:
    def test_l2_token_mappings_has_expected_tokens(self) -> None:
        assert "AERO" in L2_TOKEN_MAPPINGS

    def test_each_mapping_has_required_fields(self) -> None:
        for token, info in L2_TOKEN_MAPPINGS.items():
            assert "chain" in info, f"{token} missing chain"
            assert "contract" in info, f"{token} missing contract"

    def test_aero_is_on_base(self) -> None:
        assert L2_TOKEN_MAPPINGS["AERO"]["chain"] == "base"
```

**Step 2: Run tests**

Run: `cd py-engine && uv run pytest tests/test_l2_data_pipeline.py -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add py-engine/tests/test_l2_data_pipeline.py
git commit -m "test(icarus): update L2 pipeline tests for removed coingecko_id field"
```

---

### Task 5: Update insight synthesis test mocks

**Files:**
- Modify: `py-engine/tests/test_insight_synthesis.py:27-29,223,232`

**Step 1: Update mock source names**

These are just mock data strings — change `"coingecko"` to `"alchemy"`:

Line 28: `"sources": ["alchemy", "defillama"]` → `"sources": ["alchemy"]`
Line 29: `"sources": ["alchemy"]`
Line 223: `"sources": ["alchemy"]`
Line 232: `"sources": ["alchemy"]`

Also update the assertion on line 228 from `"2 sources"` to `"1 source"` if it checks source count, OR keep 2 sources if the test is about multi-source display. Read the test to decide — the `_compress_prices` function displays source count, so if we want it to say "1 source" instead of "2 sources", update accordingly.

Actually, the source names in insight_synthesis tests are just mock data — the code doesn't validate source names. The simplest change: replace `["coingecko", "defillama"]` with `["alchemy"]` and update the "2 sources" assertion to "1 source".

But wait — `_compress_prices` might format differently for 1 vs 2 sources. Let's keep it simple: just replace `"coingecko"` with `"alchemy"` in source arrays. Keep multi-source test with `["alchemy", "defillama"]` to test the "2 sources" display path:

Line 28: `"sources": ["alchemy", "defillama"]`
Line 29: `"sources": ["alchemy"]`
Line 223: `"sources": ["alchemy", "defillama"]`
Line 232: `"sources": ["alchemy"]`

**Step 2: Run tests**

Run: `cd py-engine && uv run pytest tests/test_insight_synthesis.py -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add py-engine/tests/test_insight_synthesis.py
git commit -m "test(icarus): update insight synthesis mocks from coingecko to alchemy"
```

---

### Task 6: Update `.env.example`

**Files:**
- Modify: `.env.example`

**Step 1: Add new env vars**

Add after line 9 (`ALCHEMY_SEPOLIA_HTTP_URL=`):

```
# ── Price Feed ─────────────────────────────────────────
ALCHEMY_API_KEY=
PRICE_FETCH_INTERVAL_SECONDS=30
```

**Step 2: Commit**

```bash
git add .env.example
git commit -m "docs(icarus): add ALCHEMY_API_KEY and PRICE_FETCH_INTERVAL_SECONDS to .env.example"
```

---

### Task 7: Run full test suite and verify

**Step 1: Run all Python tests**

Run: `cd py-engine && uv run pytest tests/ --tb=short -q`
Expected: ALL PASS, 0 failures

**Step 2: Run harness verify**

Run: `bash harness/verify.sh`
Expected: exit 0

**Step 3: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix(icarus): address any remaining test issues from price feed redesign"
```

---

## Token Reference

| Token | Alchemy symbol | DefiLlama address | Used by |
|-------|---------------|-------------------|---------|
| USDC  | USDC          | coingecko:usd-coin | LEND-001, LP-001 |
| USDT  | USDT          | coingecko:tether   | Price reference |
| DAI   | DAI           | coingecko:dai      | LP-001 |
| AERO  | AERO          | coingecko:aerodrome-finance | LP-001 (rewards) |

## What NOT to change

- `PriceFeedManager` public interface: `fetch_prices()`, `get_cached_price()`, `get_twap()`
- Redis caching logic, TWAP calculation, price history recording
- `main.py` caller at line 134 (`self.price_feed.fetch_prices()`)
- `insight_synthesis.py` caller at line 308
- DefiLlama address format (`coingecko:usd-coin` is DefiLlama's own convention, not a CoinGecko dependency)
- `L2_TOKEN_MAPPINGS` dict (still needed for `get_l2_tokens()` and `is_l2_token()`)
