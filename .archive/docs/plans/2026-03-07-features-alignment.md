# Features/System Design Alignment — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 5 alignment issues between system-design.md and features.json identified during coverage audit.

**Architecture:** All changes are document-only — system-design.md §7 and features.json. No code changes, no tests.

**Tech Stack:** JSON, Markdown

**Design doc:** `docs/plans/2026-03-07-features-alignment-design.md`

---

### Task 1: Update RISK-006 description (application-level, interim)

**Files:**
- Modify: `harness/features.json:460` (RISK-006 description)

**Step 1: Edit RISK-006 description**

Change line 460 from:
```json
"description": "Contract allowlist — application-level contract allowlist enforced by SafeWalletManager.validateOrder() before every TX execution",
```
to:
```json
"description": "Contract allowlist — application-level contract allowlist enforced by SafeWalletManager.validateOrder() before every TX execution. Interim measure until on-chain Safe guard module (RISK-009) is deployed",
```

No other fields change.

---

### Task 2: Add RISK-009 (Safe on-chain guard module)

**Files:**
- Modify: `harness/features.json` — insert new feature after RISK-008 (after line 495)

**Step 1: Insert RISK-009 after RISK-008's closing brace**

Insert after the `}` on line 495 (end of RISK-008):

```json
,
{
  "id": "RISK-009",
  "phase": "dev",
  "category": "risk",
  "description": "Safe on-chain guard module — deploy AllowlistGuard contract to enforce contract allowlist at the wallet level, unbypassable even if the application is compromised",
  "steps": [
    "Deploy AllowlistGuard contract with permitted contract addresses",
    "Attach guard to Safe via setGuard() transaction",
    "Verify on-chain guard matches application-level allowlist (RISK-006)",
    "Guard rejects transactions targeting non-allowlisted contracts at the EVM level"
  ],
  "passes": false
}
```

**Step 2: Verify JSON is valid**

Run: `python3 -c "import json; json.load(open('harness/features.json'))"`
Expected: No output (valid JSON)

---

### Task 3: Add finality step to LISTEN-003

**Files:**
- Modify: `harness/features.json:122-127` (LISTEN-003 steps)

**Step 1: Add finality step to LISTEN-003 steps array**

Add after the last step ("Configurable via environment variables..."):

```json
"Finality configuration: configurable finalityBlocks (default 12) for Base L2"
```

So the steps array becomes:
```json
"steps": [
  "Base chain event subscription via Alchemy WebSocket",
  "L2 gas estimation: OP Stack model with data posting overhead",
  "Events normalized to market-events schema with chain: base",
  "Configurable via environment variables (RPC URL, contract addresses)",
  "Finality configuration: configurable finalityBlocks (default 12) for Base L2"
],
```

---

### Task 4: Add confirmation timeout step to EXEC-001

**Files:**
- Modify: `harness/features.json:135-143` (EXEC-001 steps)

**Step 1: Add finality-aware confirmation step to EXEC-001 steps array**

Add after "All operations logged with correlation IDs":

```json
"Finality-aware confirmation: configurable timeout, waits for sufficient block confirmations"
```

So the steps array becomes:
```json
"steps": [
  "Subscribes to execution:orders Redis Stream",
  "Routes orders to protocol-specific encode modules via adapter map",
  "Pre-flight checks: deadline expiry rejection, gas ceiling enforcement",
  "Executes via SafeWalletManager (validateOrder → execute → recordSpend)",
  "Retry logic: exponential backoff, max 3 retries, non-retryable error detection",
  "Publishes results to execution:results (schema-validated)",
  "All operations logged with correlation IDs",
  "Finality-aware confirmation: configurable timeout, waits for sufficient block confirmations"
],
```

---

### Task 5: Deprecate HARNESS-004

**Files:**
- Modify: `harness/features.json:540-552` (HARNESS-004)

**Step 1: Update HARNESS-004 to deprecated state**

Replace the entire HARNESS-004 object with:

```json
{
  "id": "HARNESS-004",
  "phase": "dev",
  "category": "harness",
  "description": "DEPRECATED: Diagnostic mode — merged into HARNESS-005 (hold mode)",
  "deprecated": "Merged into HARNESS-005. Hold mode covers both API failure and irreconcilable state entry paths.",
  "steps": [],
  "passes": true
}
```

---

### Task 6: Expand HARNESS-005 with irreconcilable state entry path

**Files:**
- Modify: `harness/features.json:554-568` (HARNESS-005)

**Step 1: Update HARNESS-005 description and steps**

Change description from:
```json
"description": "Hold mode — system behavior when Claude API is unavailable (down, timeout, budget exhausted); no new decisions, auto-resume when API recovers",
```
to:
```json
"description": "Hold mode — system behavior when Claude API is unavailable or irreconcilable state is detected; no new decisions, auto-resume when trigger clears",
```

Replace steps array with:
```json
"steps": [
  "Tracked as system_status: normal | hold in Redis",
  "Entry path 1: Claude API unavailability (down, timeout after retries, budget exhausted)",
  "Entry path 2: Irreconcilable state discrepancies or critical failures during startup/runtime",
  "No new positions, rebalances, or harvests; existing positions maintained",
  "Strategy evaluation continues (reports stay fresh for Claude's return)",
  "Circuit breakers remain fully active (independent of Claude)",
  "Decision gate stays closed regardless of actionable signals",
  "Structured diagnostic logging for troubleshooting irreconcilable state",
  "Auto-resume when Claude API responds, budget resets, or state is manually reconciled"
]
```

---

### Task 7: Add dashboard to system-design.md §7 monitoring

**Files:**
- Modify: `docs/system-design.md:443`

**Step 1: Expand the monitoring paragraph**

Change line 443 from:
```markdown
Structured JSON logs to stdout. Railway captures and aggregates service logs. Alerting (Slack/Discord webhook) is a v2 concern.
```
to:
```markdown
Structured JSON logs to stdout. Railway captures and aggregates service logs. Performance dashboard tracks portfolio value, cumulative P&L, Sharpe ratio, and position breakdown. Alerting (Slack/Discord webhook) is a v2 concern.
```

---

### Task 8: Final verification and commit

**Step 1: Validate features.json**

Run: `python3 -c "import json; data = json.load(open('harness/features.json')); print(f'{len(data)} features, valid JSON')"`
Expected: `48 features, valid JSON` (was 47, added RISK-009)

**Step 2: Verify no duplicate IDs**

Run: `python3 -c "import json; data = json.load(open('harness/features.json')); ids = [f['id'] for f in data]; dupes = [x for x in ids if ids.count(x) > 1]; print('No duplicates' if not dupes else f'DUPES: {dupes}')"`
Expected: `No duplicates`

**Step 3: Commit all changes**

```bash
git add harness/features.json docs/system-design.md docs/plans/2026-03-07-features-alignment-design.md docs/plans/2026-03-07-features-alignment.md
git commit -m "docs(icarus): align features.json and system-design.md — 5 audit fixes

- RISK-006: mark as interim application-level allowlist
- RISK-009: new feature for Safe on-chain guard module
- LISTEN-003, EXEC-001: add finality-aware confirmation steps
- HARNESS-004: deprecated, merged into HARNESS-005
- HARNESS-005: expanded with irreconcilable state entry path
- system-design.md §7: add performance dashboard to monitoring

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
