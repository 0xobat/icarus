"use client";

import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import type { ExposureLimit } from "@/lib/types";

interface ExposureLimitsProps {
  limits: ExposureLimit[];
}

function headroomColor(headroom: number): string {
  if (headroom > 50) return "text-success";
  if (headroom > 25) return "text-primary";
  if (headroom > 10) return "text-warning";
  return "text-danger";
}

function barColor(headroom: number): string {
  if (headroom > 50) return "bg-success";
  if (headroom > 25) return "bg-primary";
  if (headroom > 10) return "bg-warning";
  return "bg-danger";
}

export function ExposureLimits({ limits }: ExposureLimitsProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.1 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-primary">
          Exposure Limits
        </span>
      </div>

      {/* Header row */}
      <div className="grid grid-cols-[80px_1fr_1fr_80px_80px_100px] gap-2 border-b border-border-subtle px-4 py-2">
        {["Scope", "Name", "Current", "Limit", "Headroom", "Status"].map((h) => (
          <span
            key={h}
            className="font-mono text-[8px] uppercase tracking-widest text-text-muted"
          >
            {h}
          </span>
        ))}
      </div>

      {/* Rows */}
      <div className="divide-y divide-border-subtle">
        {limits.map((limit) => {
          const ratio = (limit.current_pct / limit.limit_pct) * 100;

          return (
            <div
              key={`${limit.scope}-${limit.name}`}
              className="grid grid-cols-[80px_1fr_1fr_80px_80px_100px] gap-2 px-4 py-2.5 transition-colors hover:bg-bg-hover"
            >
              {/* Scope */}
              <span className="rounded bg-bg-elevated px-1.5 py-0.5 text-center font-mono text-[8px] uppercase text-text-tertiary">
                {limit.scope}
              </span>

              {/* Name */}
              <span className="font-mono text-[10px] font-semibold text-text-primary">
                {limit.name}
              </span>

              {/* Current Allocation */}
              <div>
                <span className="font-mono text-[10px] text-text-primary">
                  ${limit.current_allocation.toLocaleString()}
                </span>
                <span className="ml-1 font-mono text-[9px] text-text-tertiary">
                  ({limit.current_pct}%)
                </span>
              </div>

              {/* Limit */}
              <span className="font-mono text-[10px] text-text-secondary">
                {limit.limit_pct}%
              </span>

              {/* Headroom */}
              <span className={cn("font-mono text-[10px] font-semibold", headroomColor(limit.headroom))}>
                {limit.headroom}%
              </span>

              {/* Status — mini progress bar */}
              <div className="flex items-center">
                <div className="h-[3px] w-full overflow-hidden rounded-full bg-bg-elevated">
                  <div
                    className={cn("h-full rounded-full transition-all", barColor(limit.headroom))}
                    style={{ width: `${Math.min(ratio, 100)}%` }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}
