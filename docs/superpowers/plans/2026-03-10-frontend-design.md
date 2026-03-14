# Icarus Frontend Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Icarus dashboard frontend per the Cyber Rust design spec — 4 pages (Command hub + Portfolio, Decisions, Risk & Ops detail pages) with mock data.

**Architecture:** Next.js 16 App Router with client components for interactivity. All data is mock (Phase 3 handles real data). Hub + Spokes pattern — Command page is the daily driver, detail pages for deep dives. Shared components extracted for reuse across pages.

**Tech Stack:** Next.js 16, React 19, Tailwind CSS v4, Motion (framer-motion), Recharts 3, Lucide icons

**Spec:** `docs/superpowers/specs/2026-03-10-frontend-design-design.md`

---

## File Map

### Modified files

| File                                                     | Responsibility                                     |
| -------------------------------------------------------- | -------------------------------------------------- |
| `frontend/src/app/globals.css`                           | Replace teal/violet tokens with Cyber Rust palette |
| `frontend/src/app/layout.tsx`                            | Update metadata title                              |
| `frontend/src/app/page.tsx`                              | Rework Command page layout per spec §4             |
| `frontend/src/lib/mock-data.ts`                          | Expand to match all data contracts from spec       |
| `frontend/src/components/layout/sidebar.tsx`             | 4 routes (CMD/PORT/DEC/RISK), 60px width           |
| `frontend/src/components/layout/topbar.tsx`              | violet → cyan for Claude badge                     |
| `frontend/src/components/dashboard/metrics-grid.tsx`     | 2fr anchor card, sparklines, drill-down link       |
| `frontend/src/components/dashboard/strategies-panel.tsx` | Add allocation bar, pause/resume button            |
| `frontend/src/components/dashboard/circuit-breakers.tsx` | Add last_triggered timestamp, `→ RISK` link        |
| `frontend/src/components/dashboard/claude-decisions.tsx` | Rename to Claude Autopilot, add command input UI   |
| `frontend/src/components/dashboard/execution-log.tsx`    | Pending TX pinned to top with rust left-border     |
| `frontend/src/components/dashboard/system-pulse.tsx`     | Rename to Decision Loop, 3 event types with legend |
| `frontend/src/components/dashboard/portfolio-chart.tsx`  | Update colors to rust/Cyber Rust, improve tooltip  |

### New files

| File                                                      | Responsibility                                 |
| --------------------------------------------------------- | ---------------------------------------------- |
| `frontend/src/lib/types.ts`                               | TypeScript interfaces for all data contracts   |
| `frontend/src/components/dashboard/hold-mode-alert.tsx`   | Conditional amber banner (spec §4.2)           |
| `frontend/src/components/dashboard/allocation-bar.tsx`    | Stacked horizontal bar for strategy allocation |
| `frontend/src/components/shared/confirm-dialog.tsx`       | Modal confirmation for destructive actions     |
| `frontend/src/components/shared/loading-skeleton.tsx`     | Skeleton card and table primitives             |
| `frontend/src/components/shared/connection-banner.tsx`    | Connection lost red banner                     |
| `frontend/src/components/shared/stale-indicator.tsx`      | Stale data badge + opacity wrapper             |
| `frontend/src/app/portfolio/page.tsx`                     | Portfolio page (spec §5)                       |
| `frontend/src/components/portfolio/positions-table.tsx`   | Sortable positions table                       |
| `frontend/src/components/portfolio/allocation-view.tsx`   | Treemap/donut toggle visualization             |
| `frontend/src/components/portfolio/pnl-attribution.tsx`   | Horizontal bar chart for P&L by strategy       |
| `frontend/src/components/portfolio/reserve-status.tsx`    | Reserve vs minimum requirement display         |
| `frontend/src/app/decisions/page.tsx`                     | Decisions page (spec §6)                       |
| `frontend/src/components/decisions/decision-filters.tsx`  | Strategy/action/status/date range filters      |
| `frontend/src/components/decisions/decision-timeline.tsx` | Chronological decision list                    |
| `frontend/src/components/decisions/decision-detail.tsx`   | Sticky detail panel for selected decision      |
| `frontend/src/components/decisions/execution-table.tsx`   | Full TX history table (sortable, filterable)   |
| `frontend/src/app/risk/page.tsx`                          | Risk & Ops page (spec §7)                      |
| `frontend/src/components/risk/manual-overrides.tsx`       | Hold mode toggle, strategy pause, force CB     |
| `frontend/src/components/risk/circuit-breaker-card.tsx`   | Expanded CB card with sparkline history        |
| `frontend/src/components/risk/exposure-limits.tsx`        | Allocation limits table                        |
| `frontend/src/components/risk/infra-health.tsx`           | Service connection status cards                |
| `frontend/src/components/risk/system-config.tsx`          | Read-only key-value config display             |

---

## Chunk 1: Design System & Types

### Task 1: Cyber Rust color tokens

**Files:**

- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/components/dashboard/system-pulse.tsx` (hardcoded teal hex in canvas)

- [ ] **Step 1: Replace `:root` color variables**

Replace the entire `:root` block with Cyber Rust palette from spec §2.1:

```css
:root {
  /* Background scale */
  --bg-root: #0f0f11;
  --bg-surface: #0a0a0c;
  --bg-elevated: #151518;
  --bg-hover: #1a1a1e;
  --bg-active: #222226;

  /* Border — rust-tinted */
  --border-subtle: rgba(224, 122, 95, 0.08);
  --border-default: rgba(224, 122, 95, 0.15);
  --border-strong: rgba(224, 122, 95, 0.25);

  /* Primary — Rust/Terracotta */
  --primary: #e07a5f;
  --primary-dim: rgba(224, 122, 95, 0.6);
  --primary-muted: rgba(224, 122, 95, 0.15);
  --primary-ghost: rgba(224, 122, 95, 0.06);

  /* Secondary — Electric Cyan (AI/Claude only) */
  --cyan: #00b4d8;
  --cyan-dim: rgba(0, 180, 216, 0.6);
  --cyan-muted: rgba(0, 180, 216, 0.15);
  --cyan-ghost: rgba(0, 180, 216, 0.06);

  /* Semantic */
  --success: #4ade80;
  --success-muted: rgba(74, 222, 128, 0.08);
  --danger: #f87171;
  --danger-muted: rgba(248, 113, 113, 0.08);
  --warning: #fbbf24;
  --warning-muted: rgba(251, 191, 36, 0.06);

  /* Text */
  --text-primary: #d4c4b0;
  --text-secondary: #5a5a5e;
  --text-tertiary: #3a3a3e;
  --text-muted: #2a2a2e;

  /* Glow */
  --glow-primary: 0 0 20px rgba(224, 122, 95, 0.15);
  --glow-strong: 0 0 30px rgba(224, 122, 95, 0.25);
}
```

- [ ] **Step 2: Update `@theme inline` block**

Replace amber/violet with cyan tokens:

```css
@theme inline {
  --color-bg-root: var(--bg-root);
  --color-bg-surface: var(--bg-surface);
  --color-bg-elevated: var(--bg-elevated);
  --color-bg-hover: var(--bg-hover);
  --color-bg-active: var(--bg-active);

  --color-border-subtle: var(--border-subtle);
  --color-border-default: var(--border-default);
  --color-border-strong: var(--border-strong);

  --color-primary: var(--primary);
  --color-primary-dim: var(--primary-dim);
  --color-primary-muted: var(--primary-muted);
  --color-primary-ghost: var(--primary-ghost);

  --color-cyan: var(--cyan);
  --color-cyan-dim: var(--cyan-dim);
  --color-cyan-muted: var(--cyan-muted);
  --color-cyan-ghost: var(--cyan-ghost);

  --color-success: var(--success);
  --color-success-muted: var(--success-muted);
  --color-danger: var(--danger);
  --color-danger-muted: var(--danger-muted);
  --color-warning: var(--warning);
  --color-warning-muted: var(--warning-muted);

  --color-text-primary: var(--text-primary);
  --color-text-secondary: var(--text-secondary);
  --color-text-tertiary: var(--text-tertiary);
  --color-text-muted: var(--text-muted);

  --font-sans: var(--font-dm-sans);
  --font-mono: var(--font-jetbrains-mono);
  --font-display: var(--font-syne);
}
```

- [ ] **Step 3: Update hardcoded teal hex in system-pulse.tsx canvas**

Modify: `frontend/src/components/dashboard/system-pulse.tsx`

The canvas `draw()` function has hardcoded teal colors. Replace:

- `rgba(56, 189, 162, 0.3)` → `rgba(224, 122, 95, 0.3)` (primary stroke)
- `rgba(56, 189, 162, 0.08)` → `rgba(224, 122, 95, 0.08)` (glow stroke)

The `.grid-bg` and `.scanlines` in globals.css already use CSS vars — no changes needed there.

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: Build succeeds with no errors. Colors will look different but structure intact.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/globals.css frontend/src/components/dashboard/system-pulse.tsx
git commit -m "feat(icarus): replace teal/violet design system with Cyber Rust palette"
```

---

### Task 2: TypeScript data contract types

**Files:**

- Create: `frontend/src/lib/types.ts`

- [ ] **Step 1: Create types file with all data contracts from spec**

```typescript
/** Data contracts matching spec §4–7. All fields mirror the spec's data contract blocks. */

export interface HoldModeData {
  active: boolean;
  reason: string;
  since: string; // ISO8601
}

export interface DecisionLoopEvent {
  type: "eval" | "claude_call" | "tx_exec";
  timestamp: string;
  strategy_id?: string;
}

export interface MetricsData {
  portfolio_value: number;
  portfolio_change_24h_pct: number;
  portfolio_change_24h_abs: number;
  portfolio_sparkline: number[];
  drawdown_current: number;
  drawdown_limit: number;
  pnl_today: number;
  pnl_today_pct: number;
  pnl_sparkline: number[];
  tx_success_rate: number;
  tx_success_count: number;
  tx_total_count: number;
}

export interface StrategyData {
  id: string;
  name: string;
  status: "active" | "inactive";
  allocation: number;
  allocation_pct: number;
  pnl: number;
  pnl_pct: number;
  last_eval_ago: string;
  active_signals: number;
}

export interface StrategiesPanelData {
  strategies: StrategyData[];
  reserve: { amount: number; pct: number };
  total_value: number;
}

export interface Execution {
  id: string;
  tx_hash?: string;
  timestamp: string;
  type: "entry" | "exit" | "harvest" | "rebalance";
  strategy_id: string;
  description: string;
  value: number;
  status: "success" | "pending" | "failed";
}

export interface CircuitBreaker {
  name: string;
  current: number;
  limit: number;
  unit: string;
  status: "safe" | "warning" | "critical" | "triggered";
  last_triggered: string | null;
}

export interface ClaudeDecision {
  id: string;
  timestamp: string;
  action: "ENTRY" | "EXIT" | "REBALANCE" | "HOLD";
  summary: string;
  reasoning: string;
  order_count: number;
}

export interface ChartPoint {
  timestamp: string;
  value: number;
}

// Portfolio page (spec §5)
export interface Position {
  strategy_id: string;
  strategy_name: string;
  protocol: string;
  asset: string;
  amount: number;
  entry_price: number;
  current_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  portfolio_pct: number;
  entry_timestamp: string;
  tx_hash: string;
}

export interface ReserveData {
  liquid_reserve: number;
  min_reserve_requirement: number;
  reserve_pct: number;
}

// Decisions page (spec §6)
export interface DecisionDetail {
  id: string;
  timestamp: string;
  source: "claude" | "circuit_breaker";
  action: string;
  summary: string;
  reasoning: string;
  trigger_reports: Array<{ strategy_id: string; signals: string[] }>;
  orders: Array<{
    action: string;
    protocol: string;
    asset: string;
    amount: number;
    parameters: Record<string, unknown>;
  }>;
  verification: { passed: boolean; checks: string[] };
  executions: Array<{
    tx_hash: string;
    status: "success" | "pending" | "failed";
    gas_cost_usd: number;
    value: number;
  }>;
}

// Risk page (spec §7)
export interface ExposureLimit {
  scope: "protocol" | "asset";
  name: string;
  current_allocation: number;
  current_pct: number;
  limit_pct: number;
  headroom: number;
}

export interface ServiceHealth {
  name: string;
  status: "connected" | "disconnected" | "degraded";
  latency_ms: number | null;
  last_heartbeat: string;
  error_count_24h: number;
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS — types file has no runtime impact, just type definitions.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts
git commit -m "feat(icarus): add TypeScript interfaces for all frontend data contracts"
```

---

### Task 3: Expand mock data to match data contracts

**Files:**

- Modify: `frontend/src/lib/mock-data.ts`

- [ ] **Step 1: Rewrite mock-data.ts with typed data matching all contracts**

Replace entire file. Import types from `@/lib/types`. Provide mock data for:

- `holdMode: HoldModeData` (active: false for default)
- `decisionLoopEvents: DecisionLoopEvent[]` (20-30 events over last hour)
- `metricsData: MetricsData` (portfolio $847k, portfolio_sparkline: 24 hourly points, pnl_sparkline: 16 intraday points)
- `strategiesPanel: StrategiesPanelData` (LEND-001 + LP-001 from STRATEGY.md, plus reserve)
- `executions: Execution[]` (5 items, one pending pinned to top)
- `circuitBreakers: CircuitBreaker[]` (all 5 breakers with `last_triggered` field)
- `claudeDecisions: ClaudeDecision[]` (3 recent decisions)
- `positions: Position[]` (3-4 positions across strategies)
- `reserveData: ReserveData`
- `decisionDetails: DecisionDetail[]` (3 full decision records)
- `exposureLimits: ExposureLimit[]` (4 limits: 2 protocol, 2 asset)
- `serviceHealth: ServiceHealth[]` (Redis, PostgreSQL, Alchemy WS, Claude API)

Key: Use the two real strategies from STRATEGY.md (LEND-001 and LP-001) instead of the 4 fake ones.

- [ ] **Step 2: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/mock-data.ts
git commit -m "feat(icarus): expand mock data to cover all page data contracts"
```

---

## Chunk 2: Layout & Navigation

### Task 4: Update sidebar to 4-route Hub+Spokes

**Files:**

- Modify: `frontend/src/components/layout/sidebar.tsx`

- [ ] **Step 1: Update nav items array**

Replace the 6-item nav with 4 items matching spec §3:

```typescript
const navItems = [
  { href: "/", label: "CMD", icon: LayoutDashboard },
  { href: "/portfolio", label: "PORT", icon: Wallet },
  { href: "/decisions", label: "DEC", icon: ScrollText },
  { href: "/risk", label: "RISK", icon: ShieldAlert },
];
```

Remove unused icon imports (Radar, Settings).

- [ ] **Step 2: Update sidebar width from 72px to 60px**

Change `w-[72px]` to `w-[60px]` per spec §2.5. Update link width from `w-14` to `w-12`.

- [ ] **Step 3: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/sidebar.tsx
git commit -m "feat(icarus): update sidebar to 4-route Hub+Spokes navigation"
```

---

### Task 5: Update topbar — violet → cyan for Claude

**Files:**

- Modify: `frontend/src/components/layout/topbar.tsx`

- [ ] **Step 1: Replace violet color references with cyan**

In `StatusBadge` colorMap, replace:

```typescript
const colorMap = {
  primary: "text-primary border-primary/20 bg-primary-ghost",
  cyan: "text-cyan border-cyan/20 bg-cyan-ghost",
  amber: "text-amber border-amber/20 bg-amber-muted/30",
};
```

Update the CLAUDE badge to use `color="cyan"` instead of `color="violet"`.

- [ ] **Step 2: Update StatusBadge type**

Change `color` prop type from `"primary" | "violet" | "amber"` to `"primary" | "cyan" | "amber"`.

- [ ] **Step 3: Update topbar height from h-11 to h-[38px]**

Per spec §2.5.

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/layout/topbar.tsx
git commit -m "feat(icarus): update topbar with cyan Claude badge and spec dimensions"
```

---

## Chunk 3: Command Page — Top Section

### Task 6: Hold Mode Alert banner

**Files:**

- Create: `frontend/src/components/dashboard/hold-mode-alert.tsx`

- [ ] **Step 1: Create the component**

```typescript
"use client";

import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import type { HoldModeData } from "@/lib/types";

export function HoldModeAlert({ data }: { data: HoldModeData }) {
  const [snoozed, setSnoozed] = useState(false);

  if (!data.active || snoozed) return null;

  const sinceDate = new Date(data.since);
  const sinceStr = sinceDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, height: 0 }}
        animate={{ opacity: 1, height: "auto" }}
        exit={{ opacity: 0, height: 0 }}
        className="rounded border-l-[3px] border-l-warning bg-warning-muted px-4 py-3"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 rounded-full bg-warning animate-pulse-glow" />
            <span className="font-display text-xs font-bold tracking-wide text-warning uppercase">
              HOLD MODE ACTIVE
            </span>
            <span className="text-xs text-text-secondary">{data.reason}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] text-text-tertiary">
              Since {sinceStr}
            </span>
            <button
              onClick={() => {
                setSnoozed(true);
                setTimeout(() => setSnoozed(false), 10 * 60 * 1000);
              }}
              className="rounded px-2 py-1 font-mono text-[9px] text-text-secondary border border-border-subtle hover:bg-bg-hover transition-colors"
            >
              SNOOZE 10m
            </button>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/dashboard/hold-mode-alert.tsx
git commit -m "feat(icarus): add Hold Mode Alert banner component"
```

---

### Task 7: Decision Loop Pulse (rename System Pulse)

**Files:**

- Modify: `frontend/src/components/dashboard/system-pulse.tsx`

- [ ] **Step 1: Rewrite canvas to show 3 event types**

Replace the entire component. Accept `events: DecisionLoopEvent[]` prop. Render three distinct event types on a scrolling canvas per spec §4.3:

```typescript
"use client";

import { useEffect, useRef } from "react";
import type { DecisionLoopEvent } from "@/lib/types";

const EVENT_COLORS: Record<DecisionLoopEvent["type"], string> = {
  eval: "#E07A5F",       // rust ticks — small, regular
  claude_call: "#00B4D8", // cyan spikes — tall, rare
  tx_exec: "#4ade80",     // green ticks — downward, after cyan
};

const EVENT_HEIGHT: Record<DecisionLoopEvent["type"], { up: number; down: number }> = {
  eval: { up: 4, down: 0 },        // small upward tick
  claude_call: { up: 12, down: 0 }, // tall upward spike
  tx_exec: { up: 0, down: 8 },      // downward tick
};

export function DecisionLoopPulse({ events }: { events: DecisionLoopEvent[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animFrame: number;
    let offset = 0;
    const dpr = window.devicePixelRatio || 1;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
    };
    resize();

    // Map events to x positions within a 60-minute window
    const now = Date.now();
    const windowMs = 60 * 60 * 1000;

    const draw = () => {
      const w = canvas.width / dpr;
      const h = canvas.height / dpr;
      const mid = h / 2;
      ctx.clearRect(0, 0, w, h);

      // Baseline
      ctx.beginPath();
      ctx.strokeStyle = "rgba(224, 122, 95, 0.1)";
      ctx.lineWidth = 1;
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();

      // Draw events as ticks
      for (const evt of events) {
        const evtTime = new Date(evt.timestamp).getTime();
        const age = now - evtTime;
        if (age > windowMs || age < 0) continue;

        const x = ((windowMs - age) / windowMs) * w - offset % w;
        if (x < 0 || x > w) continue;

        const heights = EVENT_HEIGHT[evt.type];
        const color = EVENT_COLORS[evt.type];

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        if (heights.up > 0) {
          ctx.moveTo(x, mid);
          ctx.lineTo(x, mid - heights.up);
        }
        if (heights.down > 0) {
          ctx.moveTo(x, mid);
          ctx.lineTo(x, mid + heights.down);
        }
        ctx.stroke();
      }

      offset += 0.3;
      animFrame = requestAnimationFrame(draw);
    };

    draw();
    const obs = new ResizeObserver(resize);
    obs.observe(canvas);

    return () => {
      cancelAnimationFrame(animFrame);
      obs.disconnect();
    };
  }, [events]);

  return (
    <canvas ref={canvasRef} className="h-6 w-full" style={{ imageRendering: "auto" }} />
  );
}
```

- [ ] **Step 2: Update the wrapper in page.tsx**

Change "SYSTEM HEARTBEAT" label to "DECISION LOOP" in `page.tsx`.

- [ ] **Step 3: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/dashboard/system-pulse.tsx frontend/src/app/page.tsx
git commit -m "feat(icarus): rename System Pulse to Decision Loop with 3 event types"
```

---

### Task 8: Enhanced Metrics Grid with sparklines and anchor card

**Files:**

- Modify: `frontend/src/components/dashboard/metrics-grid.tsx`

- [ ] **Step 1: Make Portfolio Value a 2fr anchor card**

Change grid from `grid-cols-4` to `grid-cols-[2fr_1fr_1fr_1fr]`.

Portfolio Value card gets:

- Larger typography (26px value via `text-[26px]`)
- Stronger border: `border-border-strong` instead of `border-subtle`
- Absolute dollar change alongside percentage
- `→ Portfolio` drill-down link (Next.js Link to `/portfolio`)

- [ ] **Step 2: Add inline sparklines to Portfolio Value and P&L cards**

Create a small `Sparkline` component inline (just an SVG polyline, 60px wide, 20px tall):

```typescript
function Sparkline({ data, color }: { data: number[]; color: string }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 60;
  const h = 20;
  const points = data
    .map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="opacity-40">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}
```

Use `metricsData.portfolio_sparkline` and `metricsData.pnl_sparkline` from mock data.

- [ ] **Step 3: Update data source from old `metrics` import to new `metricsData`**

Replace `import { metrics } from "@/lib/mock-data"` with `import { metricsData } from "@/lib/mock-data"` and update all field references.

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/dashboard/metrics-grid.tsx
git commit -m "feat(icarus): enhance metrics grid with anchor card, sparklines, drill-down"
```

---

### Task 9: Update Portfolio Chart colors

**Files:**

- Modify: `frontend/src/components/dashboard/portfolio-chart.tsx`

- [ ] **Step 1: Replace all hardcoded teal hex values**

Find and replace:

- `#38bda2` → `#E07A5F` (rust) for positive chart color
- `rgba(56, 189, 162, ...)` → `rgba(224, 122, 95, ...)` for grid, cursor, gradient

Keep `#f87171` for negative (danger) unchanged.

Reduce timeframes to match spec §4.5: `1D / 1W / 1M / 3M / YTD / ALL` (remove 6M and 1Y, keep ALL).

- [ ] **Step 2: Add `→ Portfolio` drill-down link in header**

Add a Next.js Link in the chart header: `<Link href="/portfolio" className="font-mono text-[9px] text-primary hover:underline">→ Portfolio</Link>`

- [ ] **Step 3: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/dashboard/portfolio-chart.tsx
git commit -m "feat(icarus): update chart to Cyber Rust colors and spec timeframes"
```

---

## Chunk 4: Command Page — Bottom Panels

### Task 10: Allocation Bar component

**Files:**

- Create: `frontend/src/components/dashboard/allocation-bar.tsx`

- [ ] **Step 1: Create stacked horizontal bar**

```typescript
"use client";

import type { StrategiesPanelData } from "@/lib/types";

const rustShades = ["#E07A5F", "#c4654b", "#a85238", "#8c4028"];

export function AllocationBar({ data }: { data: StrategiesPanelData }) {
  const activeStrategies = data.strategies.filter((s) => s.status === "active");

  return (
    <div className="px-4 py-3 border-b border-border-subtle">
      {/* Bar */}
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-bg-elevated">
        {activeStrategies.map((s, i) => (
          <div
            key={s.id}
            style={{
              width: `${s.allocation_pct}%`,
              backgroundColor: rustShades[i % rustShades.length],
            }}
            className="h-full transition-all duration-500"
          />
        ))}
        <div
          style={{ width: `${data.reserve.pct}%` }}
          className="h-full bg-bg-active"
        />
      </div>
      {/* Legend */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {activeStrategies.map((s, i) => (
          <div key={s.id} className="flex items-center gap-1.5">
            <div
              className="h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: rustShades[i % rustShades.length] }}
            />
            <span className="font-mono text-[9px] text-text-secondary">
              {s.id} {s.allocation_pct}%
            </span>
          </div>
        ))}
        <div className="flex items-center gap-1.5">
          <div className="h-1.5 w-1.5 rounded-full bg-bg-active" />
          <span className="font-mono text-[9px] text-text-secondary">
            Reserve {data.reserve.pct}%
          </span>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/dashboard/allocation-bar.tsx
git commit -m "feat(icarus): add AllocationBar component for strategy capital distribution"
```

---

### Task 11: Update Strategies Panel with allocation bar + pause button

**Files:**

- Modify: `frontend/src/components/dashboard/strategies-panel.tsx`

- [ ] **Step 1: Import AllocationBar and new mock data**

Replace `import { strategies } from "@/lib/mock-data"` with `import { strategiesPanel } from "@/lib/mock-data"`.
Import `AllocationBar` from `./allocation-bar`.

- [ ] **Step 2: Add AllocationBar below the header**

Insert `<AllocationBar data={strategiesPanel} />` between the header div and the strategy rows.

- [ ] **Step 3: Add pause/resume button to each strategy row**

Add a 22px button at the end of each row:

```typescript
<button className="flex h-[22px] w-[22px] items-center justify-center rounded border border-border-subtle text-text-tertiary hover:bg-bg-hover hover:text-primary transition-colors">
  {strategy.status === "active" ? (
    <Pause className="h-2.5 w-2.5" />
  ) : (
    <Play className="h-2.5 w-2.5" />
  )}
</button>
```

- [ ] **Step 4: Update signal badge color from amber to rust-muted**

Change signal badge: `bg-amber-muted text-amber` → `bg-primary-muted text-primary`.

- [ ] **Step 5: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/dashboard/strategies-panel.tsx
git commit -m "feat(icarus): add allocation bar and pause/resume to strategies panel"
```

---

### Task 12: Update Execution Log with pending TX emphasis

**Files:**

- Modify: `frontend/src/components/dashboard/execution-log.tsx`

- [ ] **Step 1: Sort executions: pending first, then by timestamp**

```typescript
const sorted = [...executions].sort((a, b) => {
  if (a.status === "pending" && b.status !== "pending") return -1;
  if (b.status === "pending" && a.status !== "pending") return 1;
  return 0;
});
```

- [ ] **Step 2: Add visual distinction for pending rows**

Pending rows get a 2px left-border in rust and a subtle background highlight:

```typescript
className={cn(
  "group flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-bg-hover",
  tx.status === "pending" && "border-l-2 border-l-primary bg-primary-ghost"
)}
```

Import `cn` from `@/lib/utils`.

- [ ] **Step 3: Update data import to new typed mock data**

Replace `import { executions } from "@/lib/mock-data"` with `import { executions } from "@/lib/mock-data"` (same name but now typed).

Make the `VIEW ALL →` link navigate to `/decisions` using Next.js Link.

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/dashboard/execution-log.tsx
git commit -m "feat(icarus): add pending TX emphasis and drill-down link to execution log"
```

---

### Task 13: Update Circuit Breakers with last_triggered

**Files:**

- Modify: `frontend/src/components/dashboard/circuit-breakers.tsx`

- [ ] **Step 1: Add last_triggered display**

Below each breaker's value, add:

```typescript
{cb.last_triggered && (
  <span className="font-mono text-[7px] text-text-muted">
    last: {new Date(cb.last_triggered).toLocaleDateString()}
  </span>
)}
```

- [ ] **Step 2: Add "triggered" status to color map**

```typescript
const statusColors = {
  safe: { bar: "bg-primary", text: "text-primary", bg: "bg-primary-muted" },
  warning: { bar: "bg-warning", text: "text-warning", bg: "bg-warning-muted" },
  critical: { bar: "bg-danger", text: "text-danger", bg: "bg-danger-muted" },
  triggered: { bar: "bg-danger", text: "text-danger", bg: "bg-danger-muted" },
};
```

- [ ] **Step 3: Add `→ RISK` drill-down link in header**

```typescript
<Link href="/risk" className="font-mono text-[9px] text-primary hover:underline">→ Risk</Link>
```

- [ ] **Step 4: Update import to typed mock data**

Import from new typed `circuitBreakers` array.

- [ ] **Step 5: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/dashboard/circuit-breakers.tsx
git commit -m "feat(icarus): add last_triggered and drill-down to circuit breakers"
```

---

### Task 14: Claude Autopilot panel with command input

**Files:**

- Modify: `frontend/src/components/dashboard/claude-decisions.tsx`

- [ ] **Step 1: Replace violet references with cyan**

All `violet` → `cyan`:

- `border-violet/10` → `border-cyan/10`
- `text-violet` → `text-cyan`
- `bg-violet-muted` → `bg-cyan-muted`
- `Sparkles` icon color → `text-cyan`

- [ ] **Step 2: Rename header from "Claude Decisions" to "Claude Autopilot"**

- [ ] **Step 3: Add ENTRY badge color using rust (not cyan)**

Update `actionColors`:

```typescript
const actionColors = {
  REBALANCE: "bg-warning-muted text-warning border-warning/20",
  ENTRY: "bg-primary-muted text-primary border-primary/20",
  EXIT: "bg-danger-muted text-danger border-danger/20",
  HOLD: "bg-bg-elevated text-text-secondary border-border-default",
};
```

- [ ] **Step 4: Add command input at bottom of panel**

After the decisions list, add:

```typescript
<div className="border-t border-cyan/10 px-4 py-3">
  <div className="flex items-center gap-2 rounded border border-cyan/30 bg-bg-root px-3 py-2">
    <input
      type="text"
      placeholder='Ask Claude... "pause LP-001" or "why did you rebalance?"'
      className="flex-1 bg-transparent font-mono text-xs text-text-primary placeholder:text-text-muted outline-none"
      disabled
    />
    <button className="font-mono text-xs text-cyan hover:text-cyan-dim transition-colors">
      ↵
    </button>
  </div>
  <div className="mt-2 flex flex-wrap gap-1.5">
    {["Pause all", "Force hold", "Explain last trade"].map((cmd) => (
      <button
        key={cmd}
        className="rounded border border-cyan/10 bg-cyan-ghost px-2 py-0.5 font-mono text-[7px] text-cyan hover:bg-cyan-muted transition-colors"
      >
        {cmd}
      </button>
    ))}
  </div>
</div>
```

- [ ] **Step 5: Add `FULL LOG →` link to `/decisions`**

In the header, add: `<Link href="/decisions" className="font-mono text-[9px] text-cyan hover:underline">FULL LOG →</Link>`

- [ ] **Step 6: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/dashboard/claude-decisions.tsx
git commit -m "feat(icarus): rename to Claude Autopilot, add command input and cyan theme"
```

---

### Task 15: Wire up Command page with new components

**Files:**

- Modify: `frontend/src/app/page.tsx`

- [ ] **Step 1: Import new components and data**

```typescript
import { HoldModeAlert } from "@/components/dashboard/hold-mode-alert";
import { holdMode, decisionLoopEvents } from "@/lib/mock-data";
```

- [ ] **Step 2: Add HoldModeAlert between header and Decision Loop**

Insert `<HoldModeAlert data={holdMode} />` after the header motion div and before the system pulse section.

- [ ] **Step 3: Update "SYSTEM HEARTBEAT" to "DECISION LOOP"**

Already partially done in Task 7 — verify the label reads "DECISION LOOP".

- [ ] **Step 4: Verify build and visual check**

Run: `cd frontend && pnpm build`
Expected: PASS — Full Command page renders with all updated components.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx
git commit -m "feat(icarus): wire Command page with hold mode alert and updated layout"
```

---

## Chunk 5: Portfolio Page

### Task 16: Portfolio page shell and positions table

**Files:**

- Create: `frontend/src/app/portfolio/page.tsx`
- Create: `frontend/src/components/portfolio/positions-table.tsx`

- [ ] **Step 1: Create positions-table.tsx**

Sortable table component per spec §5.3. Accepts `positions: Position[]` prop.

Columns: Strategy, Protocol, Asset, Amount, Entry Price, Current Value, Unrealized P&L, % of Portfolio.

Sort by clicking column headers (client-side `useState` for sort field + direction).

P&L cell green/red based on positive/negative.

- [ ] **Step 2: Create portfolio page shell**

```typescript
"use client";

import { motion } from "motion/react";
import { PortfolioChart } from "@/components/dashboard/portfolio-chart";
import { PositionsTable } from "@/components/portfolio/positions-table";
import { positions, metricsData } from "@/lib/mock-data";

export default function PortfolioPage() {
  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="flex items-end justify-between"
      >
        <div>
          <h1 className="font-display text-2xl font-extrabold tracking-tight text-text-primary">
            PORTFOLIO
          </h1>
          <div className="mt-1 flex items-center gap-3">
            <span className="font-mono text-lg font-semibold text-text-primary">
              ${metricsData.portfolio_value.toLocaleString()}
            </span>
            <span className={`font-mono text-xs ${metricsData.portfolio_change_24h_pct >= 0 ? "text-success" : "text-danger"}`}>
              {metricsData.portfolio_change_24h_pct >= 0 ? "+" : ""}
              {metricsData.portfolio_change_24h_pct}% (24h)
            </span>
          </div>
        </div>
      </motion.div>

      {/* Chart — larger height + strategy overlay toggle (spec §5.2) */}
      <PortfolioChart height={280} showStrategyOverlay />

      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-7">
          <PositionsTable positions={positions} />
        </div>
        <div className="col-span-5 space-y-3">
          {/* Allocation view and Reserve status — Tasks 17–18 */}
          <div className="rounded-lg border border-border-subtle bg-bg-surface p-4">
            <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
              Allocation
            </span>
            <p className="mt-2 text-xs text-text-tertiary">Coming in next task</p>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add strategy overlay toggle to PortfolioChart**

Modify: `frontend/src/components/dashboard/portfolio-chart.tsx`

Per spec §5.2, the chart on the Portfolio page needs strategy overlay capability:

- Add optional `showStrategyOverlay?: boolean` and `height?: number` props to `PortfolioChart`
- When `showStrategyOverlay` is true, render a row of toggle buttons (one per strategy) below the timeframe selector
- Each toggled strategy adds a `<Line>` (Recharts) on top of the `<Area>`, using dashed stroke and a unique rust shade
- Use mock per-strategy performance data (generate in mock-data.ts alongside portfolio history)
- Track overlay state: `const [overlayStrategies, setOverlayStrategies] = useState<string[]>([])`
- Use `height` prop to set `ResponsiveContainer` height (default 200, Portfolio page passes 280)

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/portfolio/page.tsx frontend/src/components/portfolio/positions-table.tsx frontend/src/components/dashboard/portfolio-chart.tsx
git commit -m "feat(icarus): add Portfolio page with positions table and strategy overlay chart"
```

---

### Task 17: Allocation view + P&L attribution + Reserve status

**Files:**

- Create: `frontend/src/components/portfolio/allocation-view.tsx`
- Create: `frontend/src/components/portfolio/pnl-attribution.tsx`
- Create: `frontend/src/components/portfolio/reserve-status.tsx`

- [ ] **Step 1: Create allocation-view.tsx**

Simple donut chart using Recharts PieChart. Shows strategy segments + reserve.
Toggle button between "Donut" and "Treemap" views — start with donut only, treemap as placeholder text.

Accept `strategies: StrategyData[]` and `reserve: { amount: number; pct: number }` as props.

- [ ] **Step 2: Create pnl-attribution.tsx**

Horizontal bar chart using Recharts BarChart. Each bar = one strategy's P&L contribution.
Bars colored with rust shades. Shows dollar and percentage values.

Accept `strategies: StrategyData[]` prop.

- [ ] **Step 3: Create reserve-status.tsx**

Simple card showing:

- Available liquid capital (large number)
- Minimum reserve requirement
- Visual progress bar (current vs minimum)
- Headroom amount

Accept `data: ReserveData` prop.

- [ ] **Step 4: Wire into portfolio page**

Update `frontend/src/app/portfolio/page.tsx`:

- Replace the "Coming in next task" placeholder with `AllocationView` and `ReserveStatus`
- Add `PnlAttribution` in the left column below positions table

- [ ] **Step 5: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/portfolio/ frontend/src/app/portfolio/page.tsx
git commit -m "feat(icarus): add allocation view, P&L attribution, and reserve status to Portfolio"
```

---

## Chunk 6: Decisions Page

### Task 18: Decision filters and timeline

**Files:**

- Create: `frontend/src/app/decisions/page.tsx`
- Create: `frontend/src/components/decisions/decision-filters.tsx`
- Create: `frontend/src/components/decisions/decision-timeline.tsx`

- [ ] **Step 1: Create decision-filters.tsx**

Filter bar per spec §6.2:

- Strategy dropdown (All, LEND-001, LP-001)
- Action multi-select chips (ENTRY, EXIT, HARVEST, REBALANCE, HOLD)
- Status chips (success, pending, failed)
- Date range preset buttons (Today, 7d, 30d)

All state local (`useState`). Expose `filters` object and `onFilterChange` callback.

- [ ] **Step 2: Create decision-timeline.tsx**

Chronological list of decisions per spec §6.3. Each entry shows:

- Timestamp
- Action badge (color-coded)
- Source badge: "CLAUDE" (cyan) or "CB:" (rust)
- Summary, order count, execution status
- Click handler to select → calls `onSelect(id)` callback

Accept `decisions: DecisionDetail[]`, `selectedId: string | null`, `onSelect: (id: string) => void`.

- [ ] **Step 3: Create decisions page shell**

Two-column layout: timeline (7fr) + detail panel placeholder (5fr).
Wire filters → filter decisions list → pass to timeline.
Track `selectedId` state.

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/decisions/page.tsx frontend/src/components/decisions/
git commit -m "feat(icarus): add Decisions page with filters and timeline"
```

---

### Task 19: Decision detail panel + execution table

**Files:**

- Create: `frontend/src/components/decisions/decision-detail.tsx`
- Create: `frontend/src/components/decisions/execution-table.tsx`

- [ ] **Step 1: Create decision-detail.tsx**

Sticky panel (spec §6.4) — use `className="sticky top-0 max-h-screen overflow-y-auto"` to keep panel fixed while scrolling the timeline.

Shows full context for selected decision in 5 sections:

1. **Trigger** section — strategy reports and signals
2. **Claude's Reasoning** — full reasoning text (cyan-bordered section for Claude source)
3. **Orders Emitted** — action, protocol, asset, amount per order
4. **Verification Gate** — pass/reject with check list
5. **Execution Results** — TX hash, gas cost, status, value

For CB-triggered: show breaker trigger details instead of sections 2-4.

Accept `decision: DecisionDetail | null` prop. Show "Select a decision" placeholder when null.

- [ ] **Step 2: Create execution-table.tsx**

Full TX history table per spec §6.5. Same columns as spec: Status, Timestamp, TX Hash, Type, Strategy, Description, Value, Gas Cost.

TX Hash as external link placeholder (would link to BaseScan).

Accept `executions: DecisionDetail["executions"]` flattened from all decisions.

- [ ] **Step 3: Wire detail panel into decisions page**

Update `frontend/src/app/decisions/page.tsx`:

- Import `DecisionDetail` component
- Pass selected decision to detail panel in the 5fr right column
- Add execution table as a full-width section below the two-column layout (not tabbed — separate section per spec §6.5 "Below or tabbed alongside")

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/decisions/ frontend/src/app/decisions/page.tsx
git commit -m "feat(icarus): add decision detail panel and full execution table"
```

---

## Chunk 7: Risk & Ops Page

### Task 20: Manual overrides and circuit breaker cards

**Files:**

- Create: `frontend/src/app/risk/page.tsx`
- Create: `frontend/src/components/risk/manual-overrides.tsx`
- Create: `frontend/src/components/risk/circuit-breaker-card.tsx`
- Create: `frontend/src/components/shared/confirm-dialog.tsx`

- [ ] **Step 1: Create confirm-dialog.tsx**

Modal overlay with backdrop blur. Shows title, description, confirm/cancel buttons.
Confirm button is red/danger for destructive actions.

```typescript
"use client";

import { motion, AnimatePresence } from "motion/react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, description, confirmLabel = "Confirm", onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={onCancel}
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-sm rounded-lg border border-border-default bg-bg-surface p-6"
          >
            <h3 className="font-display text-sm font-bold text-text-primary">{title}</h3>
            <p className="mt-2 text-xs text-text-secondary">{description}</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={onCancel}
                className="rounded px-3 py-1.5 font-mono text-xs text-text-secondary border border-border-subtle hover:bg-bg-hover transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={onConfirm}
                className="rounded px-3 py-1.5 font-mono text-xs text-white bg-danger hover:bg-danger/80 transition-colors"
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
```

- [ ] **Step 2: Create manual-overrides.tsx**

Per spec §7.2:

- **Hold Mode Toggle** — large amber switch with current state display, triggers ConfirmDialog
- **Strategy Controls** — pause/resume per strategy (same as Command page)
- **Force Circuit Breaker** — red button per breaker, triggers ConfirmDialog

All actions are UI-only (no backend calls yet). Use `useState` for toggle states.

- [ ] **Step 3: Create circuit-breaker-card.tsx**

Expanded card per spec §7.3:

- Name + status badge (SAFE/WARNING/CRITICAL/TRIGGERED)
- Current value vs threshold (large typography)
- Progress bar
- History sparkline (use mock 24-point array)
- Last triggered timestamp + total trigger count
- Threshold value (read-only)

Accept `breaker: CircuitBreaker & { history: number[]; trigger_count: number }` prop.

- [ ] **Step 4: Create risk page shell**

Layout per spec §7.1: Manual Overrides (full-width) → two-column (CB cards 7fr | Infra Health 5fr) → two-column (Exposure 7fr | Config 5fr). Use placeholder divs for Infra Health, Exposure, Config (next task).

- [ ] **Step 5: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/risk/page.tsx frontend/src/components/risk/ frontend/src/components/shared/
git commit -m "feat(icarus): add Risk page with manual overrides and detailed circuit breaker cards"
```

---

### Task 21: Exposure limits, infra health, system config

**Files:**

- Create: `frontend/src/components/risk/exposure-limits.tsx`
- Create: `frontend/src/components/risk/infra-health.tsx`
- Create: `frontend/src/components/risk/system-config.tsx`

- [ ] **Step 1: Create exposure-limits.tsx**

Table per spec §7.4 with columns: Scope, Name, Current Allocation, Limit, Headroom, Status.
Status column shows a mini progress bar (inline, like circuit breaker bars).
Color-coded: green when >50% headroom, warning when <25%, danger when <10%.

Accept `limits: ExposureLimit[]` prop.

- [ ] **Step 2: Create infra-health.tsx**

Service cards per spec §7.5. Each service gets a row:

- Colored status dot (green/red/amber)
- Service name
- Latency (ms)
- Last heartbeat timestamp
- Error count (24h)

Accept `services: ServiceHealth[]` prop.

- [ ] **Step 3: Create system-config.tsx**

Read-only key-value display per spec §7.6:

- Chain ID, Safe wallet address
- Risk thresholds
- Strategy allocation limits
- Claude API model, token budget

Hardcoded display values (these come from env vars in production).

- [ ] **Step 4: Wire into risk page**

Update `frontend/src/app/risk/page.tsx`:

- Replace placeholder divs with `ExposureLimits`, `InfraHealth`, `SystemConfig`
- Import mock data for each

- [ ] **Step 5: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/risk/ frontend/src/app/risk/page.tsx
git commit -m "feat(icarus): add exposure limits, infra health, and system config to Risk page"
```

---

## Chunk 8: Error/Loading States & Final Polish

### Task 22: Loading skeletons and error states

**Files:**

- Create: `frontend/src/components/shared/loading-skeleton.tsx`
- Create: `frontend/src/components/shared/connection-banner.tsx`
- Create: `frontend/src/components/shared/stale-indicator.tsx`

- [ ] **Step 1: Create loading-skeleton.tsx**

Reusable skeleton primitives per spec §10:

```typescript
export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={cn("animate-pulse rounded-lg border border-border-subtle bg-bg-surface p-4", className)}>
      <div className="h-3 w-24 rounded bg-bg-elevated" />
      <div className="mt-3 h-6 w-32 rounded bg-bg-elevated" />
      <div className="mt-2 h-3 w-16 rounded bg-bg-elevated" />
    </div>
  );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-surface">
      <div className="border-b border-border-subtle px-4 py-3">
        <div className="h-3 w-32 animate-pulse rounded bg-bg-elevated" />
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3 animate-pulse">
          <div className="h-3 w-16 rounded bg-bg-elevated" />
          <div className="h-3 w-24 rounded bg-bg-elevated" />
          <div className="flex-1 h-3 rounded bg-bg-elevated" />
          <div className="h-3 w-20 rounded bg-bg-elevated" />
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Create connection-banner.tsx**

Full-width red banner per spec §10:

```typescript
"use client";

export function ConnectionBanner({ lastUpdate }: { lastUpdate: string | null }) {
  if (!lastUpdate) return null;
  return (
    <div className="border-b border-danger/20 bg-danger-muted px-4 py-2 text-center">
      <span className="font-mono text-xs text-danger">
        CONNECTION LOST — Last update {lastUpdate}
      </span>
    </div>
  );
}
```

- [ ] **Step 3: Create stale-indicator.tsx**

Per spec §10 — if data hasn't updated within 2x its expected refresh interval, show a stale badge:

```typescript
"use client";

export function StaleIndicator({ label, isStale }: { label: string; isStale: boolean }) {
  if (!isStale) return null;
  return (
    <span className="rounded bg-warning-muted px-1.5 py-0.5 font-mono text-[8px] font-medium text-warning tracking-wider">
      STALE
    </span>
  );
}

/** Wrapper that dims children when data is stale */
export function StaleWrapper({ isStale, children }: { isStale: boolean; children: React.ReactNode }) {
  return (
    <div className={isStale ? "opacity-60 transition-opacity" : "transition-opacity"}>
      {children}
    </div>
  );
}
```

Usage: Each panel header renders `<StaleIndicator>` next to the title. Wrap data content in `<StaleWrapper>` to reduce opacity to 60% when stale.

- [ ] **Step 4: Verify build**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/shared/
git commit -m "feat(icarus): add loading skeletons, connection banner, and stale data indicator"
```

---

### Task 23: Final build verification and cleanup

**Files:**

- All modified files

- [ ] **Step 1: Full build**

Run: `cd frontend && pnpm build`
Expected: PASS with no TypeScript errors or build warnings.

- [ ] **Step 2: Lint check**

Run: `cd frontend && pnpm lint`
Fix any lint errors.

- [ ] **Step 3: Remove any unused imports or dead code**

Scan for unused imports from old mock data structure.

- [ ] **Step 4: Final commit**

```bash
git add -A frontend/
git commit -m "feat(icarus): Phase 1+2 complete — Cyber Rust design system, all 4 pages with mock data"
```

---

## Summary

| Chunk                    | Tasks | What it delivers                                                              |
| ------------------------ | ----- | ----------------------------------------------------------------------------- |
| 1: Design System & Types | 1–3   | Cyber Rust palette, typed data contracts, expanded mock data                  |
| 2: Layout & Navigation   | 4–5   | 4-route sidebar, cyan Claude badge in topbar                                  |
| 3: Command Top Section   | 6–9   | Hold mode alert, Decision Loop, enhanced metrics, updated chart               |
| 4: Command Bottom Panels | 10–15 | Allocation bar, strategies, execution log, circuit breakers, Claude Autopilot |
| 5: Portfolio Page        | 16–17 | Positions table, allocation donut, P&L attribution, reserve status            |
| 6: Decisions Page        | 18–19 | Filters, timeline, detail panel, execution table                              |
| 7: Risk & Ops Page       | 20–21 | Manual overrides, expanded CB cards, exposure limits, infra health, config    |
| 8: Polish                | 22–23 | Loading skeletons, error banners, final build verification                    |
