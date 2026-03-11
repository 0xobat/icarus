"use client";

import { motion } from "motion/react";
import { Play, Pause } from "lucide-react";
import { strategies } from "@/lib/mock-data";

export function StrategiesPanel() {
  return (
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
        <span className="font-mono text-[10px] text-text-tertiary">
          {strategies.filter((s) => s.status === "active").length}/{strategies.length} running
        </span>
      </div>

      <div className="divide-y divide-border-subtle">
        {strategies.map((strategy, i) => (
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
                {strategy.signals > 0 && (
                  <span className="rounded-full bg-amber-muted px-1.5 py-0.5 font-mono text-[9px] font-medium text-amber">
                    {strategy.signals} signal{strategy.signals > 1 ? "s" : ""}
                  </span>
                )}
              </div>
              <span className="text-xs text-text-secondary">{strategy.name}</span>
            </div>

            {/* Allocation */}
            <div className="text-right">
              <div className="font-mono text-xs font-medium text-text-primary">
                ${strategy.allocation.toLocaleString()}
              </div>
              <div
                className={`font-mono text-[10px] ${
                  strategy.pnlPercent > 0 ? "text-success" : "text-text-tertiary"
                }`}
              >
                {strategy.pnlPercent > 0 ? "+" : ""}
                {strategy.pnlPercent}%
              </div>
            </div>

            {/* Last eval */}
            <div className="w-14 text-right">
              <span className="font-mono text-[10px] text-text-tertiary">
                {strategy.lastEval}
              </span>
            </div>
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}
