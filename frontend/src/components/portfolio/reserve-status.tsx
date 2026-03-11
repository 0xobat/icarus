"use client";

import { motion } from "motion/react";
import type { ReserveData } from "@/lib/types";

interface ReserveStatusProps {
  data: ReserveData;
}

export function ReserveStatus({ data }: ReserveStatusProps) {
  const headroom = data.liquid_reserve - data.min_reserve_requirement;
  const fillPct = Math.min(
    (data.liquid_reserve / (data.min_reserve_requirement * 2)) * 100,
    100
  );
  const minPct = (data.min_reserve_requirement / (data.min_reserve_requirement * 2)) * 100;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.35, duration: 0.4 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      {/* Header */}
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
          Reserve Status
        </span>
      </div>

      {/* Content */}
      <div className="p-4 space-y-4">
        {/* Main value */}
        <div>
          <p className="font-mono text-[10px] text-text-tertiary uppercase tracking-wider">
            Available Liquid Capital
          </p>
          <p className="font-mono text-xl font-semibold text-text-primary mt-1">
            ${data.liquid_reserve.toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </p>
          <p className="font-mono text-[10px] text-text-tertiary mt-0.5">
            {data.reserve_pct}% of portfolio
          </p>
        </div>

        {/* Progress bar */}
        <div>
          <div className="relative h-2 w-full rounded-full bg-bg-elevated overflow-hidden">
            {/* Fill */}
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${fillPct}%` }}
              transition={{ duration: 0.6, ease: "easeOut" }}
              className="absolute inset-y-0 left-0 rounded-full bg-success"
            />
            {/* Min requirement marker */}
            <div
              className="absolute inset-y-0 w-px bg-warning"
              style={{ left: `${minPct}%` }}
            />
          </div>

          {/* Labels below bar */}
          <div className="mt-1.5 flex items-center justify-between">
            <span className="font-mono text-[9px] text-text-tertiary">$0</span>
            <span className="font-mono text-[9px] text-warning">
              Min: ${data.min_reserve_requirement.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          </div>
        </div>

        {/* Headroom */}
        <div className="rounded border border-border-subtle bg-bg-elevated px-3 py-2">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] text-text-tertiary">Headroom</span>
            <span className={`font-mono text-xs font-medium ${headroom >= 0 ? "text-success" : "text-danger"}`}>
              +${headroom.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          </div>
          <p className="font-mono text-[9px] text-text-tertiary mt-1">
            {((headroom / data.min_reserve_requirement) * 100).toFixed(0)}% above minimum requirement
          </p>
        </div>
      </div>
    </motion.div>
  );
}
