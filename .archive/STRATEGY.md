# Icarus Strategy Definitions — v1

Chain: Base
Assets: Stablecoins only (USDC, USDbC, DAI)
Risk Profile: Conservative

---

## Aave V3 Lending Supply

**Tier: 1** | **Risk Profile: Low Risk**

ID: LEND-001

Supply stablecoins to Aave V3 on Base. Rotate to highest supply APY
market when the APY differential exceeds threshold after gas costs.

**Protocols:** Aave V3
**Chains:** Base

**Entry Conditions:**
- Target market APY exceeds current position APY by at least 0.5% after gas
- Target market has at least $1M available liquidity
- Gas cost amortized within 14 days at APY differential

**Exit Conditions:**
- Supply APY drops below 1.0%
- Protocol TVL drops more than 30% in 24 hours (circuit breaker)

**Constraints:**
- Max 70% of portfolio
- Min position size of $100 USD equivalent
- Only rotate when net monthly gain after gas exceeds $1
- Assets: USDC, USDbC only

---

## Aerodrome Stable LP

**Tier: 1** | **Risk Profile: Low Risk**

ID: LP-001

Provide liquidity to stable-stable pools on Aerodrome (USDC/USDbC,
USDC/DAI). Harvest AERO emission rewards, swap AERO to USDC,
re-deposit to compound.

**Protocols:** Aerodrome
**Chains:** Base

**Entry Conditions:**
- Pool emission APR exceeds 3.0%
- Pool TVL exceeds $500K
- AERO token has sufficient swap liquidity

**Exit Conditions:**
- Pool emission APR drops below 1.5%
- AERO price drops more than 50% in 24 hours
- Pool TVL drops below $200K

**Constraints:**
- Max 30% of portfolio
- Min position size of $100 USD equivalent
- Stable-stable pairs only (no volatile/stable pairs)
- Harvest when pending AERO value exceeds $0.50
- Swap AERO to USDC via Aerodrome router, re-deposit
