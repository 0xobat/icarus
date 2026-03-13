"use client";

import { motion } from "motion/react";
import { Play, Pause } from "lucide-react";
import { useDashboardStrategies } from "@/lib/hooks/use-dashboard";
import { SkeletonCard } from "@/components/shared/loading-skeleton";
import { StaleWrapper } from "@/components/shared/stale-indicator";
import { AllocationBar } from "./allocation-bar";

export function StrategiesPanel() {
  const { data: strategiesPanel, isLoading, stale } = useDashboardStrategies();

  if (isLoading || !strategiesPanel) {
    return <SkeletonCard />;
  }

  return (
    <StaleWrapper isStale={stale}>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35, duration: 0.4 }}
        className="rounded-lg border border-border-subtle bg-bg-surface"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
              Active Strategies
            </span>
          </div>
          <span className="font-mono text-[10px] text-text-secondary">
            {strategiesPanel.strategies.filter((s) => s.status === "active").length}/{strategiesPanel.strategies.length} running
          </span>
        </div>

        <AllocationBar data={strategiesPanel} />

        <div className="divide-y divide-border-subtle">
          {strategiesPanel.strategies.map((strategy, i) => (
            <motion.div
              key={strategy.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.4 + i * 0.06, duration: 0.3 }}
              className="group flex items-center gap-4 px-4 py-3 transition-colors hover:bg-bg-hover"
            >
              {/* Status icon */}
              <div
                className={`flex h-7 w-7 items-center justify-center rounded-md ${
                  strategy.status === "active"
                    ? "bg-primary-muted text-primary"
                    : "bg-bg-elevated text-text-tertiary"
                }`}
              >
                {strategy.status === "active" ? (
                  <Play className="h-3 w-3" fill="currentColor" />
                ) : (
                  <Pause className="h-3 w-3" />
                )}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11px] font-medium text-primary">
                    {strategy.id}
                  </span>
                  {strategy.active_signals > 0 && (
                    <span className="rounded-full bg-primary-muted px-1.5 py-0.5 font-mono text-[9px] font-medium text-primary">
                      {strategy.active_signals} signal{strategy.active_signals > 1 ? "s" : ""}
                    </span>
                  )}
                </div>
                <span className="text-xs text-text-secondary leading-snug">{strategy.name}</span>
              </div>

              {/* Allocation */}
              <div className="text-right">
                <div className="font-mono text-xs font-medium text-text-primary">
                  ${strategy.allocation.toLocaleString()}
                </div>
                <div
                  className={`font-mono text-[10px] ${
                    strategy.pnl_pct > 0 ? "text-success" : "text-text-secondary"
                  }`}
                >
                  {strategy.pnl_pct > 0 ? "+" : ""}
                  {strategy.pnl_pct}%
                </div>
              </div>

              {/* Last eval */}
              <div className="w-14 text-right">
                <span className="font-mono text-[10px] text-text-secondary">
                  {strategy.last_eval_ago}
                </span>
              </div>

              {/* Pause/resume button */}
              <button className="flex h-[22px] w-[22px] items-center justify-center rounded border border-border-subtle text-text-tertiary hover:bg-bg-hover hover:text-primary transition-colors">
                {strategy.status === "active" ? (
                  <Pause className="h-2.5 w-2.5" />
                ) : (
                  <Play className="h-2.5 w-2.5" />
                )}
              </button>
            </motion.div>
          ))}
        </div>
      </motion.div>
    </StaleWrapper>
  );
}
