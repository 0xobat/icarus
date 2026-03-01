# Icarus Strategy Definitions

Source of truth for all trading strategies. The strategy ingestion pipeline
(STRAT-008) parses this file into StrategySpec dataclasses, and the code-gen
pipeline (AI-002) generates Python strategy classes from those specs.

---

## Aave Lending Optimization

**Tier: 1** | **Risk Profile: Low Risk**

ID: STRAT-001

Rotate supplied assets across Aave V3 markets to capture the highest
risk-adjusted supply APY. Only rotate when the net improvement after gas
costs exceeds the configured threshold.

**Protocols:** Aave V3
**Chains:** Ethereum, Sepolia, Arbitrum, Base

**Entry Conditions:**
- Supply APY on target market exceeds current position APY by at least 0.5% after gas
- Target market has sufficient available liquidity
- Asset is in the whitelisted set (ETH, WETH, WBTC, USDC, USDT, DAI)

**Exit Conditions:**
- Market supply APY drops below minimum threshold
- Protocol TVL drops more than 30% in 24 hours (circuit breaker)
- Better opportunity identified in another whitelisted market

**Constraints:**
- Max 40% of portfolio in Aave protocol
- Min position size of $100 USD equivalent
- Gas cost must be amortized within 30 days at current APY differential

---

## Lido Liquid Staking

**Tier: 1** | **Risk Profile: Low Risk**

ID: STRAT-002

Stake ETH via Lido to receive stETH, then wrap to wstETH for further
DeFi deployment. Monitors staking APR and adjusts position sizing to
maintain optimal yield while preserving liquidity.

**Protocols:** Lido
**Chains:** Ethereum, Sepolia

**Entry Conditions:**
- Lido staking APR exceeds minimum threshold (currently 3.0%)
- ETH available in portfolio above minimum position size
- stETH/ETH peg deviation is within acceptable range (less than 1%)

**Exit Conditions:**
- Staking APR drops below exit threshold (currently 2.0%)
- stETH/ETH depeg exceeds 2% sustained for more than 1 hour
- Protocol TVL drops more than 30% in 24 hours

**Constraints:**
- Max 40% of portfolio in Lido protocol
- Max position adjustment of 10% per decision cycle
- Maintain minimum 15% stablecoin reserve after staking

---

## Uniswap V3 Concentrated Liquidity

**Tier: 2** | **Risk Profile: Medium Risk**

ID: STRAT-003

Manage concentrated liquidity positions on Uniswap V3. Dynamically adjust
price ranges based on volatility and price movement. Auto-collect fees
and recompound at configurable intervals.

**Protocols:** Uniswap V3
**Chains:** Ethereum, Arbitrum, Base

**Entry Conditions:**
- Target pool has sufficient TVL (minimum $1M)
- Current implied volatility supports profitable range width
- Fee APR exceeds minimum threshold after impermanent loss estimate

**Exit Conditions:**
- Price moves outside the configured range bounds
- Impermanent loss exceeds configured threshold (default 5%)
- Pool TVL drops below minimum threshold
- Better yield opportunity identified elsewhere

**Constraints:**
- Max 35% of portfolio in Uniswap V3
- Range width adapts to trailing 7-day volatility
- Rebalance only when price moves beyond 80% of range
- Fee collection at gas-optimal intervals (minimum every 24 hours)
- Max slippage 50 basis points per operation

---

## Yield Farming Auto-Compound

**Tier: 2** | **Risk Profile: Medium Risk**

ID: STRAT-004

Participate in yield farming opportunities across supported protocols.
Auto-harvest rewards at gas-optimal intervals and compound harvested
rewards back into the farming position.

**Protocols:** Aave V3, Uniswap V3, Curve, Convex
**Chains:** Ethereum, Arbitrum, Base

**Entry Conditions:**
- Farm APR exceeds minimum threshold (currently 5.0%)
- Protocol has been audited and has minimum $10M TVL
- Reward token has sufficient liquidity for harvesting

**Exit Conditions:**
- Farm APR drops below exit threshold (currently 3.0%)
- Reward token price drops more than 30% in 24 hours
- Protocol TVL drops below minimum threshold

**Constraints:**
- Max 35% of portfolio across all Tier 2 strategies
- Compound frequency optimized for gas cost vs reward accrual
- Minimum harvest value must exceed 2x gas cost
- Max 40% in any single farming protocol

---

## Flash Loan Arbitrage

**Tier: 3** | **Risk Profile: Higher Risk**

ID: STRAT-005

Detect cross-DEX price discrepancies from market events and execute
atomic flash loan arbitrage sequences. Uses flash loan executor (EXEC-007)
for zero-capital execution.

**Protocols:** Aave V3, Uniswap V3, Flashbots
**Chains:** Ethereum, Arbitrum

**Entry Conditions:**
- Price discrepancy exceeds minimum profit threshold after gas
- Flash loan liquidity available on source protocol
- Execution path validated via simulation before submission

**Exit Conditions:**
- Arbitrage is atomic — position opens and closes in same transaction
- No lingering exposure after execution

**Constraints:**
- Max 20% of portfolio in Tier 3 strategies
- Minimum net profit must exceed gas cost by 2x
- Max gas price for arbitrage transactions: 100 gwei
- Use Flashbots Protect for MEV protection
- Maximum flash loan amount: 1000 ETH equivalent

---

## Lending Rate Arbitrage

**Tier: 3** | **Risk Profile: Higher Risk**

ID: STRAT-006

Monitor lending and borrowing rates across Aave and other protocols.
Identify profitable rate differentials accounting for gas and risk.
Borrow at lower rate, supply at higher rate, capture spread.

**Protocols:** Aave V3, Compound
**Chains:** Ethereum, Sepolia, Arbitrum

**Entry Conditions:**
- Rate spread between borrow and supply exceeds minimum threshold (currently 1.0%)
- Spread has been stable for at least 1 hour
- Both protocols have sufficient liquidity for target position size

**Exit Conditions:**
- Rate spread compresses below exit threshold (currently 0.5%)
- Either protocol shows signs of instability (TVL drop, rate volatility)
- Position duration exceeds maximum hold period (default 7 days)

**Constraints:**
- Max 20% of portfolio in Tier 3 strategies
- Health factor must remain above 1.5 on borrow side
- Max position size limited by protocol utilization headroom
- Continuous monitoring of liquidation risk
- Auto-unwind when spread compresses below threshold
