# Features/System Design Alignment Fixes

**Date:** 2026-03-07
**Context:** Audit of features.json against system-design.md v4.2 found 5 alignment issues. All fixes are document-only (system design + features.json).

---

## Fix 1 — Contract Allowlist (RISK-006 + new RISK-009)

**Problem:** System design §6 says "Safe on-chain guard module" but RISK-006 describes application-level allowlist. These are different security models.

**Solution:** Keep system design as-is (on-chain guard is the target). Update RISK-006 to accurately describe the current application-level implementation as interim. Add RISK-009 for the on-chain guard deployment.

- **RISK-006** — Update description to "Application-level contract allowlist enforced by SafeWalletManager.validateOrder(). Interim measure until on-chain guard is deployed."
- **RISK-009** (new) — "Safe on-chain guard module — deploy AllowlistGuard contract to enforce contract allowlist at the wallet level, unbypassable even if the application is compromised." Steps: deploy guard contract, register permitted addresses, attach to Safe, verify application-level allowlist matches guard.

## Fix 2 — Finality-Aware TX Confirmation

**Problem:** System design risk matrix lists "Finality-aware TX confirmation" but no feature captures it. Code exists (finalityBlocks in l2-listener, confirmationTimeoutMs in transaction-builder).

**Solution:** Add steps to existing features.

- **LISTEN-003** — Add step: "Finality configuration: configurable finalityBlocks (default 12) for Base L2"
- **EXEC-001** — Add step: "Finality-aware confirmation: configurable timeout, waits for sufficient block confirmations"

## Fix 3 — HARNESS-003 (Approval Gates)

**Problem:** Feature exists but system design doesn't mention human-in-the-loop approval.

**Decision:** No changes. Feature stands on its own without explicit system design backing.

## Fix 4 — Merge HARNESS-004 into HARNESS-005

**Problem:** System design only describes hold mode. HARNESS-004 (diagnostic mode) and HARNESS-005 (hold mode) overlap — both say "no new positions, existing maintained, circuit breakers active." System design routes reconciliation failure into hold mode.

**Solution:** Merge HARNESS-004 into HARNESS-005. Hold mode gains multiple entry paths.

- **HARNESS-005** — Expand to include irreconcilable state as a trigger. Add step for diagnostic logging.
- **HARNESS-004** — Deprecate with reason: "Merged into HARNESS-005."

## Fix 5 — MON-002 (Dashboard) Added to System Design

**Problem:** MON-002 (performance dashboard) exists in features.json but system design §7 monitoring only mentions structured logs.

**Solution:** Add dashboard to system design §7 monitoring paragraph.

> "Structured JSON logs to stdout. Railway captures and aggregates service logs. Performance dashboard tracks portfolio value, cumulative P&L, Sharpe ratio, and position breakdown. Alerting (Slack/Discord webhook) is a v2 concern."
