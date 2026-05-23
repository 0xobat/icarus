"use client";

import { useState } from "react";
import { motion } from "motion/react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import type { StrategyData } from "@/lib/types";

const COLORS = ["#E07A5F", "#C4613C", "#A0522D", "#8B4513", "#6B3410"];

interface AllocationTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: { name: string; value: number; pct: number } }>;
}

function AllocationTooltip({ active, payload }: AllocationTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-md border border-border-default bg-bg-elevated px-3 py-2 shadow-lg">
      <p className="font-mono text-[10px] font-medium text-text-primary">{d.name}</p>
      <p className="font-mono text-xs text-text-secondary">
        ${d.value.toLocaleString()} ({d.pct.toFixed(1)}%)
      </p>
    </div>
  );
}

interface AllocationViewProps {
  strategies: StrategyData[];
  reserve: { amount: number; pct: number };
}

export function AllocationView({ strategies, reserve }: AllocationViewProps) {
  const [view, setView] = useState<"donut" | "treemap">("donut");

  const chartData = [
    ...strategies.map((s) => ({
      name: s.id,
      value: s.allocation,
      pct: s.allocation_pct,
    })),
    {
      name: "Reserve",
      value: reserve.amount,
      pct: reserve.pct,
    },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.3, duration: 0.4 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
          Allocation
        </span>
        <div className="flex items-center gap-0.5 rounded-md border border-border-subtle bg-bg-elevated p-0.5">
          {(["donut", "treemap"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`rounded px-2.5 py-1 font-mono text-[10px] font-medium tracking-wider transition-all duration-200 capitalize ${
                view === v
                  ? "bg-primary-muted text-primary"
                  : "text-text-tertiary hover:text-text-secondary"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="p-4">
        {view === "donut" ? (
          <div className="flex flex-col items-center">
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={chartData}
                  cx="50%"
                  cy="50%"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={2}
                  dataKey="value"
                  animationDuration={800}
                  animationEasing="ease-out"
                >
                  {chartData.map((_, i) => (
                    <Cell
                      key={i}
                      fill={i < COLORS.length ? COLORS[i] : COLORS[COLORS.length - 1]}
                      stroke="transparent"
                    />
                  ))}
                </Pie>
                <Tooltip content={<AllocationTooltip />} />
              </PieChart>
            </ResponsiveContainer>

            {/* Legend */}
            <div className="mt-2 flex flex-col gap-1.5 w-full">
              {chartData.map((d, i) => (
                <div key={d.name} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div
                      className="h-2.5 w-2.5 rounded-sm"
                      style={{ backgroundColor: i < COLORS.length ? COLORS[i] : COLORS[COLORS.length - 1] }}
                    />
                    <span className="font-mono text-[10px] text-text-secondary">{d.name}</span>
                  </div>
                  <span className="font-mono text-[10px] text-text-primary">
                    {d.pct.toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex h-[240px] items-center justify-center rounded border border-border-subtle bg-bg-elevated">
            <span className="font-mono text-[10px] text-text-tertiary">
              Treemap view — coming soon
            </span>
          </div>
        )}
      </div>
    </motion.div>
  );
}
