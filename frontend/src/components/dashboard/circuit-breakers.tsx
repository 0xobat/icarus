"use client";

import { motion } from "motion/react";
import { Shield } from "lucide-react";
import Link from "next/link";
import { useDashboardBreakers } from "@/lib/hooks/use-dashboard";
import { SkeletonCard } from "@/components/shared/loading-skeleton";
import { StaleWrapper } from "@/components/shared/stale-indicator";

const statusColors: Record<string, { bar: string; text: string; bg: string }> = {
  normal: { bar: "bg-primary", text: "text-primary", bg: "bg-primary-muted" },
  safe: { bar: "bg-primary", text: "text-primary", bg: "bg-primary-muted" },
  warning: { bar: "bg-warning", text: "text-warning", bg: "bg-warning-muted" },
  critical: { bar: "bg-danger", text: "text-danger", bg: "bg-danger-muted" },
  triggered: { bar: "bg-danger", text: "text-danger", bg: "bg-danger-muted" },
};

const defaultColors = { bar: "bg-primary", text: "text-primary", bg: "bg-primary-muted" };

export function CircuitBreakers() {
  const { data: circuitBreakersData, isLoading, stale } = useDashboardBreakers();

  if (isLoading || !circuitBreakersData) {
    return <SkeletonCard />;
  }

  return (
    <StaleWrapper isStale={stale}>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5, duration: 0.4 }}
        className="rounded-lg border border-border-subtle bg-bg-surface"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <div className="flex items-center gap-2">
            <Shield className="h-3.5 w-3.5 text-primary" strokeWidth={1.5} />
            <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
              Circuit Breakers
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] text-success">ALL NOMINAL</span>
            <Link href="/risk" className="font-mono text-[10px] text-primary hover:underline">
              &rarr; Risk
            </Link>
          </div>
        </div>

        <div className="space-y-0.5 p-3">
          {circuitBreakersData.map((cb, i) => {
            const pct = Math.min((cb.current / cb.limit) * 100, 100);
            const colors = statusColors[cb.status] ?? defaultColors;

            return (
              <motion.div
                key={cb.name}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.55 + i * 0.05, duration: 0.3 }}
                className="flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-bg-hover"
              >
                {/* Status dot */}
                <div
                  className={`h-1.5 w-1.5 rounded-full ${colors.bar} ${
                    cb.status === "warning" || cb.status === "critical" || cb.status === "triggered"
                      ? "animate-pulse-glow"
                      : ""
                  }`}
                />

                {/* Name */}
                <div className="w-28 flex flex-col">
                  <span className="text-xs text-text-primary">{cb.name}</span>
                  {cb.last_triggered && (
                    <span className="font-mono text-[9px] text-text-tertiary">
                      last: {new Date(cb.last_triggered).toLocaleDateString()}
                    </span>
                  )}
                </div>

                {/* Progress bar */}
                <div className="flex-1 h-1 rounded-full bg-bg-elevated overflow-hidden">
                  <motion.div
                    className={`h-full rounded-full ${colors.bar}`}
                    initial={{ width: 0 }}
                    animate={{ width: `${pct}%` }}
                    transition={{ delay: 0.6 + i * 0.05, duration: 0.6, ease: "easeOut" }}
                  />
                </div>

                {/* Value */}
                <div className="w-20 text-right">
                  <span className={`font-mono text-[11px] font-medium ${colors.text}`}>
                    {cb.current}
                    {cb.unit}
                  </span>
                  <span className="font-mono text-[10px] text-text-secondary">
                    /{cb.limit}
                    {cb.unit}
                  </span>
                </div>
              </motion.div>
            );
          })}
        </div>
      </motion.div>
    </StaleWrapper>
  );
}
