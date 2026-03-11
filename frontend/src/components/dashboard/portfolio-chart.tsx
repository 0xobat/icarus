"use client";

import { useState, useSyncExternalStore } from "react";
import { motion } from "motion/react";
import Link from "next/link";
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

// Strategy overlay colors — distinct rust shades
const STRATEGY_COLORS: Record<string, string> = {
  "LEND-001": "#C4613C",
  "LP-001": "#A0522D",
};

const STRATEGY_NAMES: Record<string, string> = {
  "LEND-001": "Aave V3 Lending Supply",
  "LP-001": "Aerodrome Stable LP",
};

// Generate mock data for different timeframes
function generateData(hours: number, baseValue: number) {
  const points = Math.min(hours * 2, 200);
  const data = [];
  let value = baseValue - (Math.random() * baseValue * 0.08);

  for (let i = 0; i <= points; i++) {
    const trend = (i / points) * baseValue * 0.05;
    const noise = (Math.random() - 0.45) * baseValue * 0.008;
    const cycle = Math.sin(i / (points / 6)) * baseValue * 0.015;
    value = value + trend / points + noise + cycle / points;

    const totalMinutes = (hours * 60 * i) / points;
    const h = Math.floor(totalMinutes / 60) % 24;
    const m = Math.floor(totalMinutes % 60);

    let label: string;
    if (hours <= 24) {
      label = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    } else if (hours <= 168) {
      const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      label = days[Math.floor((i / points) * 7) % 7];
    } else if (hours <= 744) {
      const day = Math.floor((i / points) * 30) + 1;
      label = `${day}${day === 1 ? "st" : day === 2 ? "nd" : day === 3 ? "rd" : "th"}`;
    } else if (hours <= 2232) {
      const week = Math.floor((i / points) * 12) + 1;
      label = `W${week}`;
    } else {
      const totalMonths = Math.round(hours / 744);
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const idx = Math.floor((i / points) * totalMonths);
      label = months[idx % 12];
    }

    data.push({ label, value: Math.round(value * 100) / 100 });
  }
  return data;
}

// Generate strategy-specific performance data (scaled proportionally)
function generateStrategyData(portfolioData: Array<{ label: string; value: number }>, strategyId: string) {
  const scale = strategyId === "LEND-001" ? 0.487 : 0.335;
  const drift = strategyId === "LEND-001" ? 0.002 : 0.005;
  return portfolioData.map((d, i) => {
    const noise = Math.sin(i * (strategyId === "LEND-001" ? 0.3 : 0.5)) * d.value * drift;
    return Math.round((d.value * scale + noise) * 100) / 100;
  });
}

const timeframes = [
  { key: "24h", label: "1D", hours: 24 },
  { key: "7d", label: "1W", hours: 168 },
  { key: "1m", label: "1M", hours: 744 },
  { key: "3m", label: "3M", hours: 2232 },
  { key: "ytd", label: "YTD", hours: 1656 },
  { key: "all", label: "ALL", hours: 8760 },
];

const BASE_VALUE = 847_293;

// Pre-generate so it's stable across renders
const datasets: Record<string, Array<Record<string, number | string>>> = {};
for (const tf of timeframes) {
  const base = generateData(tf.hours, BASE_VALUE);
  const lendData = generateStrategyData(base, "LEND-001");
  const lpData = generateStrategyData(base, "LP-001");
  datasets[tf.key] = base.map((d, i) => ({
    ...d,
    "LEND-001": lendData[i],
    "LP-001": lpData[i],
  }));
}

const strategyIds = ["LEND-001", "LP-001"];

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; dataKey: string; color: string }>;
  label?: string;
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border-default bg-bg-elevated px-3 py-2 shadow-lg">
      <p className="font-mono text-[10px] text-text-tertiary">{label}</p>
      {payload.map((entry) => (
        <p
          key={entry.dataKey}
          className="font-mono text-sm font-semibold"
          style={{ color: entry.dataKey === "value" ? "var(--text-primary)" : entry.color }}
        >
          {entry.dataKey !== "value" && (
            <span className="text-[9px] font-normal mr-1">{entry.dataKey}</span>
          )}
          ${entry.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </p>
      ))}
    </div>
  );
}

interface PortfolioChartProps {
  height?: number;
  showStrategyOverlay?: boolean;
}

export function PortfolioChart({ height = 200, showStrategyOverlay = false }: PortfolioChartProps) {
  const [active, setActive] = useState("24h");
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  const [overlayStrategies, setOverlayStrategies] = useState<string[]>([]);

  const data = datasets[active];

  const allValues = data.map((d) => d.value as number);
  const minVal = Math.min(...allValues);
  const maxVal = Math.max(...allValues);
  const padding = (maxVal - minVal) * 0.1;

  const startVal = data[0].value as number;
  const endVal = data[data.length - 1].value as number;
  const change = endVal - startVal;
  const changePct = (change / startVal) * 100;
  const isPositive = change >= 0;

  const toggleStrategy = (id: string) => {
    setOverlayStrategies((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.3, duration: 0.4 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
            Portfolio Performance
          </span>
          <span
            className={`font-mono text-xs font-medium ${
              isPositive ? "text-success" : "text-danger"
            }`}
          >
            {isPositive ? "+" : ""}
            {changePct.toFixed(2)}%
          </span>
          <span
            className={`font-mono text-[10px] ${
              isPositive ? "text-success/60" : "text-danger/60"
            }`}
          >
            ({isPositive ? "+" : ""}${Math.abs(change).toLocaleString(undefined, { maximumFractionDigits: 0 })})
          </span>
        </div>

        <div className="flex items-center gap-3">
          <Link href="/portfolio" className="font-mono text-[9px] text-primary hover:underline">
            &rarr; Portfolio
          </Link>

          {/* Strategy overlay toggles */}
          {showStrategyOverlay && (
            <div className="flex items-center gap-1 mr-2">
              {strategyIds.map((id) => (
                <button
                  key={id}
                  onClick={() => toggleStrategy(id)}
                  className={`rounded px-2 py-1 font-mono text-[9px] font-medium tracking-wider transition-all duration-200 border ${
                    overlayStrategies.includes(id)
                      ? "border-border-default text-text-primary"
                      : "border-border-subtle text-text-tertiary hover:text-text-secondary"
                  }`}
                  style={
                    overlayStrategies.includes(id)
                      ? { backgroundColor: `${STRATEGY_COLORS[id]}20`, color: STRATEGY_COLORS[id] }
                      : undefined
                  }
                >
                  {id}
                </button>
              ))}
            </div>
          )}

          {/* Timeframe selector */}
          <div className="flex items-center gap-0.5 rounded-md border border-border-subtle bg-bg-elevated p-0.5">
            {timeframes.map((tf) => (
              <button
                key={tf.key}
                onClick={() => setActive(tf.key)}
                className={`rounded px-2.5 py-1 font-mono text-[10px] font-medium tracking-wider transition-all duration-200 ${
                  active === tf.key
                    ? "bg-primary-muted text-primary"
                    : "text-text-tertiary hover:text-text-secondary"
                }`}
              >
                {tf.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart */}
      <div className="px-2 py-3">
        {!mounted ? (
          <div style={{ height }} className="animate-pulse rounded bg-bg-elevated" />
        ) : (
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
            <defs>
              <linearGradient id="portfolioGradient" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor={isPositive ? "#E07A5F" : "#f87171"}
                  stopOpacity={0.2}
                />
                <stop
                  offset="100%"
                  stopColor={isPositive ? "#E07A5F" : "#f87171"}
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="rgba(224, 122, 95, 0.05)"
              vertical={false}
            />
            <XAxis
              dataKey="label"
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 9, fill: "#4a5568", fontFamily: "var(--font-jetbrains-mono)" }}
              interval="preserveStartEnd"
              minTickGap={60}
            />
            <YAxis
              domain={[minVal - padding, maxVal + padding]}
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 9, fill: "#4a5568", fontFamily: "var(--font-jetbrains-mono)" }}
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
              width={48}
            />
            <Tooltip
              content={<CustomTooltip />}
              cursor={{
                stroke: "rgba(224, 122, 95, 0.2)",
                strokeWidth: 1,
                strokeDasharray: "4 4",
              }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={isPositive ? "#E07A5F" : "#f87171"}
              strokeWidth={1.5}
              fill="url(#portfolioGradient)"
              animationDuration={800}
              animationEasing="ease-out"
            />
            {overlayStrategies.map((id) => (
              <Line
                key={id}
                type="monotone"
                dataKey={id}
                stroke={STRATEGY_COLORS[id]}
                strokeWidth={1.5}
                strokeDasharray="6 3"
                dot={false}
                animationDuration={600}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
        )}
      </div>

      {/* Strategy overlay legend */}
      {showStrategyOverlay && overlayStrategies.length > 0 && (
        <div className="border-t border-border-subtle px-4 py-2 flex items-center gap-4">
          {overlayStrategies.map((id) => (
            <div key={id} className="flex items-center gap-1.5">
              <div
                className="h-0.5 w-4"
                style={{
                  backgroundColor: STRATEGY_COLORS[id],
                  backgroundImage: `repeating-linear-gradient(90deg, ${STRATEGY_COLORS[id]} 0, ${STRATEGY_COLORS[id]} 4px, transparent 4px, transparent 6px)`,
                }}
              />
              <span className="font-mono text-[9px] text-text-tertiary">
                {id} — {STRATEGY_NAMES[id]}
              </span>
            </div>
          ))}
        </div>
      )}
    </motion.div>
  );
}
