"use client";

import { useState, useEffect } from "react";
import { motion } from "motion/react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

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
      // ~1 month: show day of month
      const day = Math.floor((i / points) * 30) + 1;
      label = `${day}${day === 1 ? "st" : day === 2 ? "nd" : day === 3 ? "rd" : "th"}`;
    } else if (hours <= 2232) {
      // ~3 months: show week labels
      const week = Math.floor((i / points) * 12) + 1;
      label = `W${week}`;
    } else if (hours <= 4464) {
      // ~6 months: show month abbreviations
      const months = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar"];
      const idx = Math.floor((i / points) * 6);
      label = months[Math.min(idx, 5)];
    } else {
      // 1yr / YTD / All: show month abbreviations
      const totalMonths = Math.round(hours / 744);
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const idx = Math.floor((i / points) * totalMonths);
      label = months[idx % 12];
    }

    data.push({ label, value: Math.round(value * 100) / 100 });
  }
  return data;
}

const timeframes = [
  { key: "24h", label: "24H", hours: 24 },
  { key: "7d", label: "7D", hours: 168 },
  { key: "1m", label: "1M", hours: 744 },
  { key: "3m", label: "3M", hours: 2232 },
  { key: "6m", label: "6M", hours: 4464 },
  { key: "1y", label: "1Y", hours: 8760 },
  { key: "ytd", label: "YTD", hours: 1656 }, // ~Mar 9 = ~69 days into year
  { key: "all", label: "ALL", hours: 8760 },
];

const BASE_VALUE = 847_293;

// Pre-generate so it's stable across renders
const datasets: Record<string, ReturnType<typeof generateData>> = {};
for (const tf of timeframes) {
  datasets[tf.key] = generateData(tf.hours, BASE_VALUE);
}

function CustomTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border-default bg-bg-elevated px-3 py-2 shadow-lg">
      <p className="font-mono text-[10px] text-text-tertiary">{label}</p>
      <p className="font-mono text-sm font-semibold text-text-primary">
        ${payload[0].value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </p>
    </div>
  );
}

export function PortfolioChart() {
  const [active, setActive] = useState("24h");
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const data = datasets[active];

  const minVal = Math.min(...data.map((d) => d.value));
  const maxVal = Math.max(...data.map((d) => d.value));
  const padding = (maxVal - minVal) * 0.1;

  const startVal = data[0].value;
  const endVal = data[data.length - 1].value;
  const change = endVal - startVal;
  const changePct = (change / startVal) * 100;
  const isPositive = change >= 0;

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

      {/* Chart */}
      <div className="px-2 py-3">
        {!mounted ? (
          <div className="h-[200px] animate-pulse rounded bg-bg-elevated" />
        ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
            <defs>
              <linearGradient id="portfolioGradient" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor={isPositive ? "#38bda2" : "#f87171"}
                  stopOpacity={0.2}
                />
                <stop
                  offset="100%"
                  stopColor={isPositive ? "#38bda2" : "#f87171"}
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="rgba(56, 189, 162, 0.05)"
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
                stroke: "rgba(56, 189, 162, 0.2)",
                strokeWidth: 1,
                strokeDasharray: "4 4",
              }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={isPositive ? "#38bda2" : "#f87171"}
              strokeWidth={1.5}
              fill="url(#portfolioGradient)"
              animationDuration={800}
              animationEasing="ease-out"
            />
          </AreaChart>
        </ResponsiveContainer>
        )}
      </div>
    </motion.div>
  );
}
