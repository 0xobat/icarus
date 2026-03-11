"use client";

import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import type { CircuitBreaker } from "@/lib/types";

interface CircuitBreakerCardProps {
  breaker: CircuitBreaker & { history: number[]; trigger_count: number };
}

const statusColors: Record<string, { bar: string; text: string; bg: string; badge: string }> = {
  safe: {
    bar: "bg-primary",
    text: "text-primary",
    bg: "bg-primary-muted",
    badge: "bg-success-muted text-success border-success/20",
  },
  warning: {
    bar: "bg-warning",
    text: "text-warning",
    bg: "bg-warning-muted",
    badge: "bg-warning-muted text-warning border-warning/20",
  },
  critical: {
    bar: "bg-danger",
    text: "text-danger",
    bg: "bg-danger-muted",
    badge: "bg-danger-muted text-danger border-danger/20",
  },
  triggered: {
    bar: "bg-danger",
    text: "text-danger",
    bg: "bg-danger-muted",
    badge: "bg-danger-muted text-danger border-danger/20",
  },
};

function HistorySparkline({ data, limit, color }: { data: number[]; limit: number; color: string }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, limit);
  const w = 120;
  const h = 24;
  const points = data
    .map((v, i) => `${(i / (data.length - 1)) * w},${h - (v / max) * h}`)
    .join(" ");
  const thresholdY = h - (limit / max) * h;

  return (
    <svg width={w} height={h} className="mt-2">
      {/* Threshold line */}
      <line
        x1={0}
        y1={thresholdY}
        x2={w}
        y2={thresholdY}
        stroke="rgba(248, 113, 113, 0.3)"
        strokeWidth="1"
        strokeDasharray="3,2"
      />
      {/* Sparkline */}
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

export function CircuitBreakerCard({ breaker }: CircuitBreakerCardProps) {
  const colors = statusColors[breaker.status] ?? statusColors.safe;
  const ratio = Math.min((breaker.current / breaker.limit) * 100, 100);
  const lastTriggeredStr = breaker.last_triggered
    ? new Date(breaker.last_triggered).toLocaleDateString([], {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "Never";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-lg border border-border-subtle bg-bg-surface p-4 hover:border-border-default transition-colors"
    >
      {/* Header: Name + Status Badge */}
      <div className="flex items-center justify-between">
        <span className="font-display text-xs font-bold text-text-primary">{breaker.name}</span>
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-wider",
            colors.badge
          )}
        >
          {breaker.status}
        </span>
      </div>

      {/* Current vs Threshold */}
      <div className="mt-3 flex items-baseline gap-1">
        <span className={cn("font-mono text-xl font-semibold", colors.text)}>
          {breaker.current}
        </span>
        <span className="font-mono text-[10px] text-text-muted">
          / {breaker.limit}{breaker.unit}
        </span>
      </div>

      {/* Progress bar */}
      <div className="mt-2 h-[3px] w-full overflow-hidden rounded-full bg-bg-elevated">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${ratio}%` }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className={cn("h-full rounded-full", colors.bar)}
        />
      </div>

      {/* History sparkline */}
      <HistorySparkline
        data={breaker.history}
        limit={breaker.limit}
        color={
          breaker.status === "safe"
            ? "#E07A5F"
            : breaker.status === "warning"
              ? "#fbbf24"
              : "#f87171"
        }
      />

      {/* Meta row */}
      <div className="mt-3 flex items-center justify-between">
        <div>
          <span className="font-mono text-[8px] uppercase tracking-widest text-text-muted">
            Last triggered
          </span>
          <p className="font-mono text-[9px] text-text-tertiary">{lastTriggeredStr}</p>
        </div>
        <div className="text-right">
          <span className="font-mono text-[8px] uppercase tracking-widest text-text-muted">
            Total triggers
          </span>
          <p className="font-mono text-[9px] text-text-tertiary">{breaker.trigger_count}</p>
        </div>
      </div>

      {/* Threshold config */}
      <div className="mt-2 rounded bg-bg-elevated px-2 py-1">
        <span className="font-mono text-[8px] uppercase tracking-widest text-text-muted">
          Threshold
        </span>
        <span className="ml-2 font-mono text-[9px] text-text-secondary">
          {breaker.limit}
          {breaker.unit}
        </span>
      </div>
    </motion.div>
  );
}
