"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "motion/react";
import { MetricsGrid } from "@/components/dashboard/metrics-grid";
import { StrategiesPanel } from "@/components/dashboard/strategies-panel";
import { ExecutionLog } from "@/components/dashboard/execution-log";
import { CircuitBreakers } from "@/components/dashboard/circuit-breakers";
import { ClaudeDecisions } from "@/components/dashboard/claude-decisions";
import { DecisionLoopPulse } from "@/components/dashboard/system-pulse";
import { PortfolioChart } from "@/components/dashboard/portfolio-chart";
import { HoldModeAlert } from "@/components/dashboard/hold-mode-alert";
import { SkeletonCard } from "@/components/shared/loading-skeleton";
import { StaleIndicator } from "@/components/shared/stale-indicator";
import { useSystemStatus } from "@/lib/hooks/use-risk";
import { useDashboardMetrics } from "@/lib/hooks/use-dashboard";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import type { DecisionLoopEvent } from "@/lib/types";

export default function Home() {
  const { data: holdModeData, isLoading: holdLoading, stale: holdStale } = useSystemStatus();
  const { data: metricsData, isLoading: metricsLoading, stale: metricsStale } = useDashboardMetrics();
  const { subscribe } = useEventStream();
  const [loopEvents, setLoopEvents] = useState<DecisionLoopEvent[]>([]);

  const handleEvent = useCallback((data: unknown) => {
    const event = data as DecisionLoopEvent;
    if (event && event.type && event.timestamp) {
      setLoopEvents((prev) => [...prev.slice(-199), event]);
    }
  }, []);

  useEffect(() => {
    const unsub = subscribe("decision_loop", handleEvent);
    return unsub;
  }, [subscribe, handleEvent]);

  return (
    <div className="mx-auto max-w-[1400px] space-y-4">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="flex items-end justify-between"
      >
        <div>
          <h1 className="font-display text-2xl font-extrabold tracking-tight text-text-primary">
            COMMAND CENTER
          </h1>
          <p className="mt-0.5 font-mono text-xs text-text-secondary">
            {">"} System operating autonomously. All parameters nominal.
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3 rounded-md border border-border-subtle bg-bg-surface px-3 py-1.5">
            {metricsLoading ? (
              <span className="font-mono text-[10px] text-text-secondary">Loading...</span>
            ) : metricsData ? (
              <>
                <StatChip label="TX SUCCESS" value={`${metricsData.tx_success_rate}%`} color="text-success" />
                <div className="h-3 w-px bg-border-subtle" />
                <StatChip label="DRAWDOWN" value={`-${metricsData.drawdown_current}%`} color="text-amber" />
                <div className="h-3 w-px bg-border-subtle" />
                <StatChip label="GAS" value="--" color="text-text-secondary" />
              </>
            ) : (
              <>
                <StatChip label="TX SUCCESS" value="--" color="text-text-secondary" />
                <div className="h-3 w-px bg-border-subtle" />
                <StatChip label="DRAWDOWN" value="--" color="text-text-secondary" />
                <div className="h-3 w-px bg-border-subtle" />
                <StatChip label="GAS" value="--" color="text-text-secondary" />
              </>
            )}
            {metricsStale && <StaleIndicator isStale />}
          </div>
        </div>
      </motion.div>

      {/* Hold Mode Alert */}
      {holdLoading ? (
        <SkeletonCard />
      ) : holdModeData ? (
        <>
          {holdStale && <StaleIndicator isStale />}
          <HoldModeAlert data={holdModeData} />
        </>
      ) : null}

      {/* Decision Loop pulse */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2, duration: 0.6 }}
        className="rounded-lg border border-border-subtle bg-bg-surface px-4 py-2"
      >
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-4">
            <span className="font-mono text-[10px] text-text-secondary tracking-wider">
              DECISION LOOP
            </span>
            {/* Legend */}
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <div className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: "#E07A5F" }} />
                <span className="font-mono text-[9px] text-text-secondary">EVAL</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: "#00B4D8" }} />
                <span className="font-mono text-[9px] text-text-secondary">CLAUDE</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: "#4ade80" }} />
                <span className="font-mono text-[9px] text-text-secondary">TX</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="h-1 w-1 rounded-full bg-success animate-breathe" />
            <span className="font-mono text-[10px] text-success">LIVE</span>
          </div>
        </div>
        <DecisionLoopPulse events={loopEvents} />
      </motion.div>

      {/* Metrics */}
      <MetricsGrid />

      {/* Portfolio chart */}
      <PortfolioChart />

      {/* Two-column layout: Strategies + Execution | Circuit Breakers + Claude */}
      <div className="grid grid-cols-12 gap-3">
        {/* Left column — primary content */}
        <div className="col-span-7 space-y-3">
          <StrategiesPanel />
          <ExecutionLog />
        </div>

        {/* Right column — system status */}
        <div className="col-span-5 space-y-3">
          <CircuitBreakers />
          <ClaudeDecisions />
        </div>
      </div>
    </div>
  );
}

function StatChip({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="font-mono text-[10px] text-text-secondary tracking-wider">{label}</span>
      <span className={`font-mono text-[11px] font-medium ${color}`}>{value}</span>
    </div>
  );
}
