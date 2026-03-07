# Price Feed Redesign: Alchemy Primary + DefiLlama Fallback

Date: 2026-03-07

## Problem

The price feed uses CoinGecko as primary source. CoinGecko's free tier rate-limits
at ~10-30 req/min, but the decision loop fires every ~2 seconds (every Base block),
causing constant 429 errors. The dual-source averaging (CoinGecko + DefiLlama) is
overkill for stablecoins. Additionally, Etherscan V1 gas API is deprecated.

## Decision

Replace CoinGecko with Alchemy Token Prices API as primary. Keep DefiLlama as
fallback. Add configurable fetch interval to avoid unnecessary API calls.

## API Details

**Alchemy Token Prices API:**
- Endpoint: `GET https://api.g.alchemy.com/prices/v1/{apiKey}/tokens/by-symbol?symbols=USDC&symbols=DAI...`
- Auth: API key in URL path (reuses existing Alchemy key)
- Response: `{"data": [{"symbol": "USDC", "prices": [{"currency": "usd", "value": "1.0000", "lastUpdatedAt": "..."}]}]}`
- Confirmed working for: USDC, USDT, DAI, AERO

**DefiLlama (fallback):**
- Endpoint: `GET https://coins.llama.fi/prices/current/{coins}`
- Auth: None needed
- Response: `{"coins": {"coingecko:usd-coin": {"price": 1.0, "symbol": "USDC", "timestamp": ..., "confidence": 0.99}}}`

## Changes

### `py-engine/data/price_feed.py`

**Remove:**
- `_fetch_coingecko()` method
- `SUPPORTED_TOKENS` dict (CoinGecko ID mapping)
- `fetch_l2_prices()` method (CoinGecko-only L2 price fetcher)
- Dual-source cross-check logic in `fetch_prices()` (averaging, deviation check)

**Add:**
- `_fetch_alchemy()` — calls Alchemy Token Prices API
- `ALCHEMY_API_KEY` from env (or extracted from existing `ALCHEMY_SEPOLIA_API_KEY`)
- `PRICE_FETCH_INTERVAL_SECONDS` env var (default 30)
- Cache-freshness check at top of `fetch_prices()` — skip API calls if all prices are younger than interval

**Revised `fetch_prices()` flow:**
1. Check cache age — if fresh, return cached prices (no API call)
2. Try `_fetch_alchemy()` — on success, cache and return
3. On failure, try `_fetch_defillama()` — on success, cache and return
4. On both failure, return stale cache or empty dict

### `.env.example` + `.env.local`

Add:
- `ALCHEMY_API_KEY=` (new dedicated var)
- `PRICE_FETCH_INTERVAL_SECONDS=30`

### `py-engine/tests/test_price_feed.py`

- Update mocks from CoinGecko format to Alchemy format
- Remove dual-source deviation tests
- Add tests: cache-hit skip, Alchemy fail -> DefiLlama fallback, both-fail -> stale cache

### No changes needed

- `PriceFeedManager` public interface (`fetch_prices()`, `get_cached_price()`, `get_twap()`)
- Redis caching, TWAP calculation, price history
- `main.py`, `insight_synthesis.py` callers
- DefiLlama address format (`coingecko:usd-coin` is DefiLlama's convention)

## Token List

| Token | Alchemy symbol | DefiLlama address | Used by |
|-------|---------------|-------------------|---------|
| USDC  | USDC          | coingecko:usd-coin | LEND-001, LP-001 |
| USDT  | USDT          | coingecko:tether   | Price reference |
| DAI   | DAI           | coingecko:dai      | LP-001 |
| AERO  | AERO          | coingecko:aerodrome-finance | LP-001 (rewards) |
