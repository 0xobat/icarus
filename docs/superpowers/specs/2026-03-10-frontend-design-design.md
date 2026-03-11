# Icarus Frontend Design Specification

**Version:** 1.0 · **Date:** 2026-03-10

---

## 1. Overview

Dashboard for a solo operator monitoring an autonomous DeFi asset management bot. The frontend is a **monitor + manual override** tool — primarily observability with critical "break glass" controls (pause strategies, force hold mode, trigger circuit breakers).

**User:** Single operator (no auth, no multi-user).
**Data layer:** Each component defines its data contract (shape, source, refresh frequency). API/WebSocket implementation is a separate design task — it is a hard prerequisite for Phase 3. The frontend will need read-only access to PostgreSQL and Redis subscribe capability, either via direct access or an API layer.

**Visual redesign:** This spec replaces the existing teal/violet color scheme (`globals.css`) with a new "Cyber Rust" palette. All existing components will be refactored to the new tokens in Phase 1. This is a deliberate breaking visual change.

### Stack

| Technology | Version | Purpose |
|-----------|---------|---------|
| Next.js | 16.x | Framework (App Router) |
| React | 19.x | UI |
| Tailwind CSS | 4.x | Styling via CSS custom properties |
| Motion | (framer-motion) | Animations |
| Recharts | 3.x | Charts |
| Lucide | — | Icons |

---

## 2. Design System

### 2.1 Color Palette — Cyber Rust

Dark industrial aesthetic. Warm rust primary for system/data elements, electric cyan exclusively for AI/Claude. Hot/cold contrast conveys "advanced machinery."

**Backgrounds**

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-root` | `#0F0F11` | Page background |
| `--bg-surface` | `#0a0a0c` | Cards, sidebar, topbar |
| `--bg-elevated` | `#151518` | Hover states, input fields, nested containers |
| `--bg-hover` | `#1a1a1e` | Interactive hover |
| `--bg-active` | `#222226` | Active/pressed state |

**Primary — Rust/Terracotta**

| Token | Hex | Usage |
|-------|-----|-------|
| `--primary` | `#E07A5F` | Primary accent, strategy IDs, active nav, borders |
| `--primary-dim` | `rgba(224,122,95,0.6)` | Secondary emphasis |
| `--primary-muted` | `rgba(224,122,95,0.15)` | Badge backgrounds, subtle fills |
| `--primary-ghost` | `rgba(224,122,95,0.06)` | Ghost backgrounds, topbar badges |

**Secondary — Electric Cyan (AI/Claude)**

| Token | Hex | Usage |
|-------|-----|-------|
| `--cyan` | `#00B4D8` | Claude elements exclusively: decisions, order badges, status, command input |
| `--cyan-dim` | `rgba(0,180,216,0.6)` | Secondary emphasis |
| `--cyan-muted` | `rgba(0,180,216,0.15)` | Badge backgrounds |
| `--cyan-ghost` | `rgba(0,180,216,0.06)` | Ghost backgrounds |

**Semantic**

| Token | Hex | Usage |
|-------|-----|-------|
| `--success` | `#4ade80` | Positive P&L, TX success, system online |
| `--success-muted` | `rgba(74,222,128,0.08)` | Success badge backgrounds |
| `--danger` | `#f87171` | Negative P&L, TX failure, critical state |
| `--danger-muted` | `rgba(248,113,113,0.08)` | Danger badge backgrounds |
| `--warning` | `#fbbf24` | Gas spike warnings, hold mode |
| `--warning-muted` | `rgba(251,191,36,0.06)` | Warning backgrounds |

**Text**

| Token | Hex | Usage |
|-------|-----|-------|
| `--text-primary` | `#d4c4b0` | Headings, values — warm bone, not cold white |
| `--text-secondary` | `#5a5a5e` | Descriptions, secondary labels |
| `--text-tertiary` | `#3a3a3e` | Timestamps, disabled text, subtle info |
| `--text-muted` | `#2a2a2e` | Axis labels, placeholder text |

**Borders**

| Token | Usage |
|-------|-------|
| `--border-subtle` | `rgba(224,122,95,0.08)` — Default card/divider borders |
| `--border-default` | `rgba(224,122,95,0.15)` — Hover, emphasized borders |
| `--border-strong` | `rgba(224,122,95,0.25)` — Active states, anchor elements |

### 2.2 Typography

| Role | Font | Weight | Usage |
|------|------|--------|-------|
| Display | Syne | 600–800 | Section headers, page titles |
| Body | DM Sans | 400–700 | Descriptions, body text |
| Data | JetBrains Mono | 400–600 | Values, timestamps, IDs, labels, badges |

**Scale:**
- Page title: 17px, font-weight 800, letter-spacing 0.04em
- Section header: 10px uppercase, font-weight 700, letter-spacing 0.06em
- Data value (large): 26px (anchor card) / 20px (standard card)
- Data value (inline): 11px monospace
- Label: 8–9px uppercase, letter-spacing 0.08–0.12em
- Badge text: 8px monospace, letter-spacing 0.04–0.05em
- Timestamp: 9px monospace, `--text-tertiary`

### 2.3 Component Patterns

**Card**
- `background: --bg-surface`
- `border: 1px solid --border-subtle`
- `border-radius: 4px`
- Header row with `border-bottom: --border-subtle`, contains section title (left) and meta/action (right)
- Hover: `border-color: --border-default`

**Badge**
- Rounded (2–3px radius), 8px monospace text
- Color-coded: `background: {color}-muted`, `color: {color}`, optional `border: 1px solid {color}/0.15`
- Types: action (ENTRY, EXIT, HARVEST, REBALANCE), status (PENDING), count (1 order)

**Progress Bar (Circuit Breakers)**
- Track: `--bg-elevated`, height 3px, rounded
- Fill: color-coded by status (rust=safe, warning=yellow, danger=red)
- Animated width on mount

**Status Indicator**
- 4–5px circle, color matches status
- Active states get `box-shadow: 0 0 Npx {color}` glow
- Warning/critical states pulse

**Drill-down Link**
- 8–9px, `--primary` color, right-aligned in section header
- Format: `→ PageName` or `VIEW ALL →`

**Interactive Controls**
- Pause/resume: 22px square button, `--border-subtle` border, icon centered
- Command input: `--bg-root` background, `--cyan` border, monospace placeholder
- Quick-action chips: 7px cyan text, ghost background, 1px border

### 2.4 Animations

- **Mount:** Staggered fade-in (opacity 0→1, y 12→0), 0.3–0.4s duration, 0.05–0.08s stagger
- **Progress bars:** Width 0→N% on mount, 0.6s ease-out
- **Status dots:** `pulse-glow` (opacity oscillation) for warning states, `breathe` for live indicators
- **Hover:** 0.2s transition on border-color and background
- **Decision Loop pulse:** Continuous canvas animation, scrolling left-to-right

### 2.5 Layout

- **Sidebar:** 60px wide, fixed left. Icon + 7px label per nav item. Active item has 2px left-border accent + muted background. System status dot at bottom.
- **Topbar:** 38px height. Left: branding (ICARUS + version). Center: status badges (shields, claude). Right: uptime + notification bell.
- **Content area:** 16–20px padding. Max-width unconstrained (fills available space).
- **Grid system:** CSS Grid. Command page uses `7fr 5fr` two-column below the chart.

---

## 3. Page Structure — Hub + Spokes

4 pages total. Command is the daily driver (80% of use), detail pages for deep dives.

| Page | Route | Sidebar Label | Purpose |
|------|-------|---------------|---------|
| Command | `/` | CMD | Dashboard hub — everything at a glance |
| Portfolio | `/portfolio` | PORT | Positions, allocation, P&L attribution |
| Decisions | `/decisions` | DEC | Claude audit trail + full execution log |
| Risk & Ops | `/risk` | RISK | Circuit breakers, exposure, infra health, overrides |

---

## 4. Command Page (`/`)

The hub. Designed for a quick glance to answer: "Is everything okay? What happened recently? Does anything need attention?"

### 4.1 Layout (top to bottom)

```
┌─────────────────────────────────────────────────┐
│ Header: "COMMAND CENTER" + stat chips (TX/DD/GAS)│
├─────────────────────────────────────────────────┤
│ [Hold Mode Alert Banner — conditional]           │
├─────────────────────────────────────────────────┤
│ Decision Loop Pulse                              │
├─────────────────────────────────────────────────┤
│ Metrics: [Portfolio 2fr][Drawdown][P&L][TX Rate] │
├─────────────────────────────────────────────────┤
│ Portfolio Performance Chart                      │
├────────────────────────┬────────────────────────┤
│ Strategies Panel (7fr) │ Circuit Breakers (5fr)  │
│ + Allocation Bar       │ + Last Triggered        │
├────────────────────────┼────────────────────────┤
│ Execution Log          │ Claude Autopilot        │
│ + Pending TX emphasis  │ + Command Input         │
└────────────────────────┴────────────────────────┘
```

### 4.2 Hold Mode Alert Banner

Conditional — only renders when `system_status === "hold"`.

- Full-width amber alert between header and Decision Loop
- Left: pulsing amber dot + "HOLD MODE ACTIVE" + reason text
- Right: "Since {timestamp}" + snooze button (hides banner for 10 minutes, does NOT clear hold mode — hold mode clears automatically when the triggering condition resolves per system design)
- `border-left: 3px solid --warning`
- `background: --warning-muted`

**Data contract:**
```
{
  active: boolean
  reason: string       // "Claude API timeout after 3 retries"
  since: ISO8601       // When hold mode started
}
Source: Redis (system_status key)
Refresh: Real-time (subscribe)
```

### 4.3 Decision Loop Pulse

Renamed from "System Heartbeat." Visualizes actual system activity, not decorative.

- Canvas animation scrolling left to right
- Three event types with legend:
  - Rust ticks (small, regular): strategy evaluations
  - Cyan spikes (tall, rare): Claude API calls
  - Green ticks (downward, after cyan): TX executions
- Header: "DECISION LOOP" label + legend + LIVE indicator

**Data contract:**
```
{
  events: Array<{
    type: "eval" | "claude_call" | "tx_exec"
    timestamp: ISO8601
    strategy_id?: string
  }>
}
Source: Redis Stream (derived from market:events, execution:orders, execution:results)
Refresh: Real-time (subscribe)
```

### 4.4 Metrics Grid

4 cards in a `2fr 1fr 1fr 1fr` grid. Portfolio card is the visual anchor.

**Portfolio Value (anchor — 2fr)**
- Larger typography (26px value)
- Background sparkline (24h trend)
- Shows absolute dollar change alongside percentage
- `→ Portfolio` drill-down link
- `border: --border-strong` (stronger than other cards)

**Drawdown**
- Current drawdown from peak
- Shows limit threshold (20%)
- Standard card styling

**Today's P&L**
- Background sparkline (intraday trend)
- Green/red based on positive/negative

**TX Success Rate**
- Success count / total format (142/144)
- 24h window

**Data contract:**
```
{
  portfolio_value: number
  portfolio_change_24h_pct: number
  portfolio_change_24h_abs: number
  portfolio_sparkline: number[]     // 24 hourly points
  drawdown_current: number
  drawdown_limit: number
  pnl_today: number
  pnl_today_pct: number
  pnl_sparkline: number[]          // intraday points
  tx_success_rate: number
  tx_success_count: number
  tx_total_count: number
}
Source: PostgreSQL (portfolio positions) + Redis (cached calculations)
Refresh: 10s polling or real-time on execution:results events
```

### 4.5 Portfolio Performance Chart

Area chart with timeframe selector and interactive tooltip.

- **Timeframe toggles:** Pill group — 1D / 1W / 1M / 3M / YTD / ALL
- **Chart:** Area chart (Recharts), rust stroke + gradient fill
- **Tooltip (hover):** Crosshair vertical line + dot on line + tooltip card showing date, exact value, % change from period open
- **Y-axis:** Dollar labels ($820k, $840k, etc.)
- **X-axis:** Time labels appropriate to timeframe
- **Color:** Green stroke/fill when period is positive, red when negative
- `→ Portfolio` drill-down link in header

**Data contract:**
```
{
  timeframe: "1d" | "1w" | "1m" | "3m" | "ytd" | "all"
  data: Array<{ timestamp: ISO8601, value: number }>
}
Source: PostgreSQL (trade history + position snapshots)
Refresh: On timeframe change + every 60s for active timeframe
```

### 4.6 Strategies Panel

Strategy list with allocation visualization and inline controls.

**Allocation Bar (top of panel)**
- Stacked horizontal bar showing capital distribution
- Segments: one per active strategy + reserve
- Legend below with strategy ID, percentage
- Each strategy segment gets a slightly different shade of rust for distinction

**Strategy Rows**
- Status icon: ▶ (active, rust background) or ⏸ (paused, muted)
- Strategy ID (monospace, rust) + name (secondary text)
- Signal badge: "{N} signal" in rust-muted when actionable signals present
- Allocation (dollar amount) + P&L percentage
- Last eval timestamp (e.g., "12s")
- **Pause/resume button** — 22px inline toggle (manual override)

**Data contract:**
```
{
  strategies: Array<{
    id: string               // "LEND-001"
    name: string
    status: "active" | "inactive"
    allocation: number       // USD
    allocation_pct: number
    pnl: number
    pnl_pct: number
    last_eval_ago: string    // "12s"
    active_signals: number
  }>
  reserve: { amount: number, pct: number }
  total_value: number
}
Source: PostgreSQL (strategy statuses, positions) + Redis (latest eval timestamps)
Refresh: 10s polling
```

### 4.7 Execution Log

Recent transactions with pending emphasis.

- **Pending TXs pinned to top** — rust left-border (2px), background highlight, distinct PENDING badge
- **Completed TXs below** — chronological, most recent first
- Each row: status icon (✓/◷/✕), timestamp, type badge (ENTRY/EXIT/HARVEST/REBALANCE), strategy ID, description, value
- Type badges color-coded: entry=rust, exit=danger, harvest=success, rebalance=warning (cyan reserved for Claude elements)
- `VIEW ALL →` links to Decisions page
- Shows most recent 5 transactions

**Data contract:**
```
{
  executions: Array<{
    id: string
    tx_hash?: string
    timestamp: ISO8601
    type: "entry" | "exit" | "harvest" | "rebalance"
    strategy_id: string
    description: string
    value: number
    status: "success" | "pending" | "failed"
  }>
}
Source: PostgreSQL (trade history) + Redis (pending from execution:orders)
Refresh: Real-time on execution:results events
```

### 4.8 Circuit Breakers

Compact view of all 5 breakers with status and history.

Each breaker row:
- Status dot (color-coded, pulsing if warning/critical)
- Name
- Progress bar (current / limit)
- Value display: `{current}/{limit}{unit}`
- "Last triggered" timestamp below (7px, muted)

Breakers: Portfolio Drawdown, Position Loss, Gas Spike, TX Failure Rate, Protocol TVL Drop.

**Data contract:**
```
{
  breakers: Array<{
    name: string
    current: number
    limit: number
    unit: string           // "%", "gwei", etc.
    status: "safe" | "warning" | "critical" | "triggered"
    last_triggered: ISO8601 | null
  }>
}
Source: Redis (circuit breaker state) + PostgreSQL (trigger history)
Refresh: Real-time on market:events
```

### 4.9 Claude Autopilot

Decision log + interactive command input. Cyan-bordered to visually distinguish as AI territory.

**Decision Entries (most recent 2–3)**
- Timestamp + action badge (ENTRY/EXIT/REBALANCE/HOLD) + order count badge
- Summary line (bold)
- Reasoning line (tertiary text)
- `FULL LOG →` links to Decisions page

**Command Input**
- Text input at bottom of panel: `--bg-root` background, `--cyan` border
- Placeholder: `Ask Claude... "pause LP-001" or "why did you rebalance?"`
- Submit button (↵)
- Quick-action chips below: pre-built commands (pause strategies, force hold, explain last trade)

**Scope note:** The command input is a **v2 feature**. Initial implementation shows the UI but routes commands to pre-defined actions (pause, hold, explain). Free-form Claude chat is a future enhancement.

**Data contract:**
```
{
  decisions: Array<{
    id: string              // "DEC-8942"
    timestamp: ISO8601
    action: "ENTRY" | "EXIT" | "REBALANCE" | "HOLD"
    summary: string
    reasoning: string
    order_count: number
  }>
}
Source: PostgreSQL (decision audit log)
Refresh: Real-time on new decisions
```

---

## 5. Portfolio Page (`/portfolio`)

Deep dive into positions and performance. Drill-down from Command's portfolio card and chart.

### 5.1 Layout

```
┌─────────────────────────────────────────────────┐
│ Header: "PORTFOLIO" + total value + 24h change   │
├─────────────────────────────────────────────────┤
│ Performance Chart (full-width, larger)            │
│ + Strategy overlay toggle                        │
├──────────────────────────┬──────────────────────┤
│ Positions Table (7fr)    │ Allocation View (5fr) │
│                          │ (treemap or donut)    │
├──────────────────────────┼──────────────────────┤
│ P&L Attribution (7fr)    │ Reserve Status (5fr)  │
└──────────────────────────┴──────────────────────┘
```

### 5.2 Performance Chart

Same chart component as Command, but larger (250–300px height) and with strategy overlay capability.

- Toggle to overlay individual strategy performance lines on the portfolio curve
- Each strategy line uses a different shade/style (solid vs dashed)
- Legend shows which strategies are overlaid

### 5.3 Positions Table

Sortable table of all current positions.

| Column | Description |
|--------|-------------|
| Strategy | Strategy ID + name |
| Protocol | Aave V3, Aerodrome, etc. |
| Asset | USDC, USDbC, etc. |
| Amount | Token amount |
| Entry Price | Price at entry |
| Current Value | USD value |
| Unrealized P&L | Dollar + percentage |
| % of Portfolio | Allocation weight |

- Sortable by clicking column headers
- Rows expandable to show entry timestamp, TX hash, position history

**Data contract:**
```
{
  positions: Array<{
    strategy_id: string
    strategy_name: string
    protocol: string
    asset: string
    amount: number
    entry_price: number
    current_value: number
    unrealized_pnl: number
    unrealized_pnl_pct: number
    portfolio_pct: number
    entry_timestamp: ISO8601
    tx_hash: string
  }>
}
Source: PostgreSQL (positions table)
Refresh: 30s polling
```

### 5.4 Allocation Visualization

Toggle between two views:
- **Treemap** — blocks sized by allocation, colored by strategy
- **Donut chart** — segments by strategy + reserve

Both views show: strategy ID, dollar amount, percentage on hover/label.

### 5.5 P&L Attribution

Horizontal bar chart showing each strategy's contribution to total P&L.

- Timeframe selector matching the performance chart
- Bars colored by strategy (rust shades)
- Shows both dollar and percentage contribution

### 5.6 Reserve Status

- Available liquid capital (dollar amount)
- Minimum reserve requirement (from env vars)
- Visual bar showing current vs minimum
- Margin to threshold (how much headroom)

**Data contract:**
```
{
  liquid_reserve: number
  min_reserve_requirement: number
  reserve_pct: number
}
Source: PostgreSQL (portfolio positions) + env vars
Refresh: 30s polling
```

---

## 6. Decisions Page (`/decisions`)

Full audit trail for Claude decisions and TX execution. Drill-down from Command's Autopilot panel and execution log.

### 6.1 Layout

```
┌─────────────────────────────────────────────────┐
│ Header: "DECISIONS" + total calls today + filters│
├─────────────────────────────────────────────────┤
│ Filters: Strategy | Action | Status | Date range │
├──────────────────────────┬──────────────────────┤
│ Decision Timeline (7fr)  │ Detail Panel (5fr)    │
│                          │ (shows selected       │
│                          │  decision details)    │
└──────────────────────────┴──────────────────────┘
```

### 6.2 Filters

Row of filter controls:
- **Strategy:** dropdown (LEND-001, LP-001, All)
- **Action:** multi-select chips (ENTRY, EXIT, HARVEST, REBALANCE, HOLD)
- **Status:** chips (success, pending, failed)
- **Date range:** preset buttons (Today, 7d, 30d) + custom range picker

### 6.3 Decision Timeline

Chronological list (newest first) of all Claude API decisions and CB-triggered actions.

Each entry:
- Timestamp
- Action badge (color-coded)
- Source badge: "CLAUDE" (cyan) or "CB:{type}" (rust) for circuit breaker actions
- Summary line
- Order count
- Execution status indicator (all succeeded, partial, failed)
- Click to select → populates Detail Panel

### 6.4 Detail Panel

Shows full context for selected decision. Sticky on scroll.

**Sections:**
1. **Trigger** — which strategy reports opened the decision gate, which signals were actionable
2. **Claude's Reasoning** — full reasoning text from the API response
3. **Orders Emitted** — each order with: action, protocol, asset, amount, parameters
4. **Verification Gate** — pass/reject status, which checks were applied
5. **Execution Results** — TX hash (linked to BaseScan), gas cost, confirmation status, final value

For CB-triggered actions: sections 2–4 differ (no Claude reasoning, no verification gate — just breaker trigger details).

### 6.5 Execution Log (Full)

Below or tabbed alongside the timeline — complete TX history.

| Column | Description |
|--------|-------------|
| Status | ✓ / ◷ / ✕ |
| Timestamp | ISO format |
| TX Hash | Linked to BaseScan |
| Type | ENTRY / EXIT / HARVEST / REBALANCE |
| Strategy | ID |
| Description | Human-readable summary |
| Value | USD |
| Gas Cost | USD equivalent |

Sortable, filterable (same filters as above).

**Data contract:**
```
{
  decisions: Array<{
    id: string
    timestamp: ISO8601
    source: "claude" | "circuit_breaker"
    action: string
    summary: string
    reasoning: string
    trigger_reports: Array<{ strategy_id: string, signals: Signal[] }>
    orders: Array<{
      action: string
      protocol: string
      asset: string
      amount: number
      parameters: object
    }>
    verification: { passed: boolean, checks: string[] }
    executions: Array<{
      tx_hash: string
      status: "success" | "pending" | "failed"
      gas_cost_usd: number
      value: number
    }>
  }>
}
Source: PostgreSQL (decision audit log, trade history)
Refresh: Real-time on new decisions + execution:results
```

---

## 7. Risk & Ops Page (`/risk`)

System health, safety rails, and manual overrides. Drill-down from Command's circuit breakers and system status.

### 7.1 Layout

```
┌─────────────────────────────────────────────────┐
│ Header: "RISK & OPERATIONS" + system status badge│
├─────────────────────────────────────────────────┤
│ Manual Overrides (full-width, prominent)          │
├──────────────────────────┬──────────────────────┤
│ Circuit Breakers (7fr)   │ Infra Health (5fr)    │
│ (detailed cards)         │                       │
├──────────────────────────┼──────────────────────┤
│ Exposure Limits (7fr)    │ System Config (5fr)   │
└──────────────────────────┴──────────────────────┘
```

### 7.2 Manual Overrides

Prominent section at the top — this is the "break glass" area.

- **Hold Mode Toggle** — Large, prominent switch. Amber when active, muted when inactive. Shows current state + reason if active. Requires confirmation dialog.
- **Strategy Controls** — Pause/resume for each strategy (duplicated from Command for convenience). Shows current status.
- **Force Circuit Breaker** — Button per breaker to manually trigger. Red, requires confirmation dialog ("This will unwind position X. Confirm?").

### 7.3 Circuit Breakers (Detailed)

Each breaker gets its own card (not the compact row format from Command).

Per card:
- Name + current status badge (SAFE / WARNING / CRITICAL / TRIGGERED)
- Current value vs threshold (large typography)
- Progress bar (same as Command)
- **History sparkline** — value over last 24h, showing how close it's been to threshold
- Last triggered: timestamp + trigger count (total historical)
- Threshold config: display current threshold value (read-only)

### 7.4 Exposure Limits

Table showing allocation limits vs current state.

| Column | Description |
|--------|-------------|
| Scope | Per-protocol / per-asset |
| Name | Aave V3, USDC, etc. |
| Current Allocation | USD + percentage |
| Limit | Maximum allowed |
| Headroom | Remaining before limit |
| Status | Visual bar + color |

**Data contract:**
```
{
  limits: Array<{
    scope: "protocol" | "asset"
    name: string
    current_allocation: number
    current_pct: number
    limit_pct: number
    headroom: number
  }>
}
Source: PostgreSQL (positions) + env vars (limits)
Refresh: 30s polling
```

### 7.5 Infrastructure Health

Connection status for each external dependency.

| Service | Metrics |
|---------|---------|
| Redis | Connected/disconnected, latency, last heartbeat |
| PostgreSQL | Connected/disconnected, latency, last heartbeat |
| Alchemy WebSocket | Connected/disconnected, last event received, reconnection count |
| Claude API | Online/hold, last successful call, error count (24h) |

Each service: colored status dot + name + latency + last successful interaction timestamp.

**Data contract:**
```
{
  services: Array<{
    name: string
    status: "connected" | "disconnected" | "degraded"
    latency_ms: number | null
    last_heartbeat: ISO8601
    error_count_24h: number
  }>
}
Source: Redis (health check keys) + py-engine health endpoint
Refresh: 5s polling
```

### 7.6 System Config

Read-only display of current runtime configuration. Not editable from the UI — configuration changes happen via env vars and redeployment.

Displayed as key-value pairs:
- Chain ID
- Safe wallet address
- Risk thresholds (drawdown limit, position loss limit, gas spike multiplier, etc.)
- Strategy allocation limits
- Claude API model, token budget
- Eval intervals per strategy

---

## 8. Shared Components

Components reused across multiple pages.

| Component | Used On | Description |
|-----------|---------|-------------|
| `Sidebar` | All | 60px icon nav, 4 routes, system status dot |
| `Topbar` | All | Branding, shields/claude status badges, uptime, notifications |
| `PerformanceChart` | Command, Portfolio | Recharts area chart with timeframe selector, tooltip, responsive |
| `StatusBadge` | Topbar, Risk | Pill-shaped badge with icon, label, value, color variant |
| `CircuitBreakerRow` | Command | Compact breaker display (dot + name + bar + value + last triggered) |
| `CircuitBreakerCard` | Risk | Expanded breaker card with sparkline history |
| `StrategyRow` | Command | Strategy info + allocation + P&L + pause button |
| `ExecutionRow` | Command, Decisions | TX row with status, type badge, strategy, description, value |
| `AllocationBar` | Command, Portfolio | Stacked horizontal bar with legend |
| `HoldModeAlert` | Command | Conditional amber banner |
| `ConfirmDialog` | Risk | Modal confirmation for destructive actions |

---

## 9. Data Refresh Strategy

| Tier | Frequency | Components |
|------|-----------|------------|
| Real-time | WebSocket / SSE subscription | Decision Loop pulse, hold mode status, new decisions, TX results |
| Fast polling | 10s | Metrics grid, strategy panel, circuit breakers |
| Standard polling | 30s | Positions table, allocation view, exposure limits |
| On-demand | User action | Chart timeframe changes, filter changes, page navigation |

Initial implementation uses polling. WebSocket/SSE upgrade is a v2 concern — the data contracts are designed to support either.

---

## 10. Error, Loading & Stale States

For a monitoring dashboard, degraded states are critical UX — the operator needs to know when data is unreliable.

**Loading skeletons:** Every card and panel has a skeleton variant: `--bg-elevated` pulsing rectangles matching the layout shape of the loaded state. Used on initial page load and timeframe changes.

**Stale data indicator:** If a component's data source hasn't updated within 2x its expected refresh interval, show a subtle amber "STALE" badge in the component header + reduce opacity of stale values to 60%. This maps to the system design's staleness threshold concept (60s default for price data).

**Connection lost banner:** If the frontend loses connection to the data source (API/WebSocket down), show a red banner below the topbar: "CONNECTION LOST — Last update {timestamp}." All data remains visible but dimmed. Banner auto-dismisses on reconnection.

**Empty states:** Components with no data show a centered message in `--text-tertiary`: "No {items} yet" (e.g., "No decisions yet", "No positions yet"). No illustration — just text, matching the minimal aesthetic.

**Error states:** API errors show inline in the affected component: red-tinted border + "Failed to load {component}. Retrying..." with automatic retry (3 attempts, exponential backoff). After exhaustion, show "Failed to load" with a manual retry button.

---

## 11. Implementation Phases

**Phase 1 — Design system + Command page refinement**
- Update `globals.css` with Cyber Rust tokens
- Refactor existing components to new color scheme
- Add: allocation bar, hold mode alert, decision loop pulse, sparkline cards, interactive chart tooltip, Claude command input

**Phase 2 — Detail pages**
- Portfolio page: positions table, allocation viz, P&L attribution, reserve status
- Decisions page: timeline, detail panel, filters, full execution log
- Risk & Ops page: override controls, detailed breaker cards, exposure limits, infra health, system config

**Phase 3 — Data layer**
- Define API routes or WebSocket endpoints matching data contracts
- Replace mock data with real fetching
- Add loading skeletons, error states, empty states

**Phase 4 — Polish**
- Responsive layout (not mobile-first, but functional at common desktop widths)
- Keyboard shortcuts for common actions
- Notification system (topbar bell → dropdown)
