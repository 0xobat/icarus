"use client";

import { motion } from "motion/react";
import { MetricsGrid } from "@/components/dashboard/metrics-grid";
import { StrategiesPanel } from "@/components/dashboard/strategies-panel";
import { ExecutionLog } from "@/components/dashboard/execution-log";
import { CircuitBreakers } from "@/components/dashboard/circuit-breakers";
import { ClaudeDecisions } from "@/components/dashboard/claude-decisions";
import { SystemPulse } from "@/components/dashboard/system-pulse";
import { PortfolioChart } from "@/components/dashboard/portfolio-chart";

export default function Home() {
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
          <p className="mt-0.5 font-mono text-xs text-text-tertiary">
            {">"} System operating autonomously. All parameters nominal.
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3 rounded-md border border-border-subtle bg-bg-surface px-3 py-1.5">
            <StatChip label="TX SUCCESS" value="98.2%" color="text-success" />
            <div className="h-3 w-px bg-border-subtle" />
            <StatChip label="DRAWDOWN" value="-4.2%" color="text-amber" />
            <div className="h-3 w-px bg-border-subtle" />
            <StatChip label="GAS" value="45 gwei" color="text-text-secondary" />
          </div>
        </div>
      </motion.div>

      {/* System pulse */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2, duration: 0.6 }}
        className="rounded-lg border border-border-subtle bg-bg-surface px-4 py-2"
      >
        <div className="flex items-center justify-between mb-1">
          <span className="font-mono text-[9px] text-text-tertiary tracking-wider">
            SYSTEM HEARTBEAT
          </span>
          <div className="flex items-center gap-1.5">
            <div className="h-1 w-1 rounded-full bg-success animate-breathe" />
            <span className="font-mono text-[9px] text-success">LIVE</span>
          </div>
        </div>
        <SystemPulse />
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
      <span className="font-mono text-[9px] text-text-tertiary tracking-wider">{label}</span>
      <span className={`font-mono text-[10px] font-medium ${color}`}>{value}</span>
    </div>
  );
}
