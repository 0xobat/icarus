"use client";

import { motion } from "motion/react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import type { StrategyData } from "@/lib/types";

const RUST_SHADES = ["#E07A5F", "#C4613C", "#A0522D", "#8B4513"];

interface PnlTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: { name: string; pnl: number; pnl_pct: number } }>;
}

function PnlTooltip({ active, payload }: PnlTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-md border border-border-default bg-bg-elevated px-3 py-2 shadow-lg">
      <p className="font-mono text-[10px] font-medium text-text-primary">{d.name}</p>
      <p className={`font-mono text-xs ${d.pnl >= 0 ? "text-success" : "text-danger"}`}>
        {d.pnl >= 0 ? "+" : ""}${d.pnl.toLocaleString()} ({d.pnl >= 0 ? "+" : ""}{d.pnl_pct.toFixed(1)}%)
      </p>
    </div>
  );
}

interface PnlAttributionProps {
  strategies: StrategyData[];
}

export function PnlAttribution({ strategies }: PnlAttributionProps) {
  const data = strategies.map((s) => ({
    name: s.id,
    fullName: s.name,
    pnl: s.pnl,
    pnl_pct: s.pnl_pct,
  }));

  const totalPnl = strategies.reduce((sum, s) => sum + s.pnl, 0);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.4, duration: 0.4 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
          P&L Attribution
        </span>
        <span className={`font-mono text-xs font-medium ${totalPnl >= 0 ? "text-success" : "text-danger"}`}>
          Total: {totalPnl >= 0 ? "+" : ""}${totalPnl.toLocaleString()}
        </span>
      </div>

      {/* Chart */}
      <div className="px-4 py-3">
        <ResponsiveContainer width="100%" height={120}>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 40, left: 0, bottom: 0 }}
          >
            <XAxis
              type="number"
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 9, fill: "#4a5568", fontFamily: "var(--font-jetbrains-mono)" }}
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
            />
            <YAxis
              type="category"
              dataKey="name"
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 10, fill: "#5a5a5e", fontFamily: "var(--font-jetbrains-mono)" }}
              width={70}
            />
            <Tooltip content={<PnlTooltip />} cursor={{ fill: "rgba(224, 122, 95, 0.04)" }} />
            <Bar dataKey="pnl" radius={[0, 3, 3, 0]} animationDuration={800}>
              {data.map((_, i) => (
                <Cell
                  key={i}
                  fill={RUST_SHADES[i % RUST_SHADES.length]}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>

        {/* Value labels */}
        <div className="mt-2 flex flex-col gap-1">
          {data.map((d, i) => (
            <div key={d.name} className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div
                  className="h-2 w-2 rounded-sm"
                  style={{ backgroundColor: RUST_SHADES[i % RUST_SHADES.length] }}
                />
                <span className="font-mono text-[10px] text-text-secondary">
                  {d.name}
                </span>
              </div>
              <span className={`font-mono text-[10px] font-medium ${d.pnl >= 0 ? "text-success" : "text-danger"}`}>
                {d.pnl >= 0 ? "+" : ""}${d.pnl.toLocaleString()} ({d.pnl >= 0 ? "+" : ""}{d.pnl_pct.toFixed(1)}%)
              </span>
            </div>
          ))}
        </div>
      </div>
    </motion.div>
  );
}
