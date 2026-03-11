"use client";

import { motion } from "motion/react";
import { Sparkles } from "lucide-react";
import { claudeDecisions } from "@/lib/mock-data";

const actionColors = {
  REBALANCE: "bg-amber-muted text-amber border-amber/20",
  ENTRY: "bg-primary-muted text-primary border-primary/20",
  EXIT: "bg-danger-muted text-danger border-danger/20",
  HOLD: "bg-bg-elevated text-text-secondary border-border-default",
};

export function ClaudeDecisions() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.55, duration: 0.4 }}
      className="rounded-lg border border-violet/10 bg-bg-surface"
    >
      <div className="flex items-center justify-between border-b border-violet/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-violet" strokeWidth={1.5} />
          <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
            Claude Decisions
          </span>
        </div>
        <span className="font-mono text-[10px] text-text-tertiary">
          {claudeDecisions.length} calls today
        </span>
      </div>

      <div className="divide-y divide-border-subtle">
        {claudeDecisions.map((decision, i) => (
          <motion.div
            key={decision.id}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.6 + i * 0.06, duration: 0.3 }}
            className="group px-4 py-3 transition-colors hover:bg-bg-hover cursor-pointer"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[10px] text-text-tertiary">
                  {decision.timestamp}
                </span>
                <span
                  className={`rounded border px-1.5 py-0.5 font-mono text-[9px] font-medium tracking-wider ${
                    actionColors[decision.action as keyof typeof actionColors] ||
                    actionColors.HOLD
                  }`}
                >
                  {decision.action}
                </span>
                <span className="rounded bg-violet-muted px-1.5 py-0.5 font-mono text-[9px] text-violet">
                  {decision.orders} order{decision.orders > 1 ? "s" : ""}
                </span>
              </div>
              <span className="font-mono text-[10px] text-text-tertiary opacity-0 transition-opacity group-hover:opacity-100">
                {decision.id} →
              </span>
            </div>
            <p className="mt-1.5 text-xs font-medium text-text-primary">
              {decision.summary}
            </p>
            <p className="mt-0.5 text-[11px] text-text-tertiary leading-relaxed">
              {decision.reasoning}
            </p>
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}
