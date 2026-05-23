# contracts/

Solidity contracts deployed by Icarus.

## AllowlistGuard.sol

**Provenance:** Verbatim copy from `.archive/contracts/AllowlistGuard.sol`. The v4.2 RISK-009
feature passes per `.archive/harness/features.json`; the production deployment on Base
mainnet has been verified against the v4.2 application-level allowlist.

**Validated copy-back protocol (W1D5):**
1. ✅ v4.2 launch-ready state: RISK-009 feature passes in `.archive/harness/features.json`.
2. ✅ Verbatim copy via `cp .archive/contracts/AllowlistGuard.sol contracts/AllowlistGuard.sol`
   (zero diff confirmed at copy time).
3. ⏳ Local solc/forge compile check: deferred to W11 cutover prep when the foundry toolchain
   is set up. The contract is *not* recompiled or redeployed for v2 — the existing on-chain
   instance continues to enforce the wallet-level allowlist.

**Why we trust it without local compilation:**
- Already deployed and operating in production at a verified address.
- Source matches the deployed bytecode (verified on BaseScan).
- v2 adds no Solidity changes — the multi-chain split keeps Base wallet semantics intact
  (Solana uses Squads's on-chain policy module separately, not this Solidity contract).
