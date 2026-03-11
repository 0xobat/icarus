"use client";

import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import type { DecisionDetail } from "@/lib/types";

interface DecisionTimelineProps {
  decisions: DecisionDetail[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const actionColors: Record<string, string> = {
  ENTRY: "bg-primary-muted text-primary border-primary/20",
  EXIT: "bg-danger-muted text-danger border-danger/20",
  HARVEST: "bg-success-muted text-success border-success/20",
  REBALANCE: "bg-warning-muted text-warning border-warning/20",
  HOLD: "bg-bg-elevated text-text-secondary border-border-default",
};

function getExecutionStatus(executions: DecisionDetail["executions"]): {
  label: string;
  color: string;
} {
  if (executions.length === 0) return { label: "no orders", color: "text-text-tertiary" };
  const allSuccess = executions.every((e) => e.status === "success");
  const anyFailed = executions.some((e) => e.status === "failed");
  const anyPending = executions.some((e) => e.status === "pending");
  if (allSuccess) return { label: "all succeeded", color: "text-success" };
  if (anyFailed) return { label: "failed", color: "text-danger" };
  if (anyPending) return { label: "pending", color: "text-warning" };
  return { label: "partial", color: "text-warning" };
}

export function DecisionTimeline({ decisions, selectedId, onSelect }: DecisionTimelineProps) {
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-surface">
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-primary">
          Decision Timeline
        </span>
      </div>
      <div className="divide-y divide-border-subtle">
        {decisions.map((decision, i) => {
          const execStatus = getExecutionStatus(decision.executions);
          const ts = new Date(decision.timestamp);
          const timeStr = ts.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
          });

          return (
            <motion.button
              key={decision.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: i * 0.05 }}
              onClick={() => onSelect(decision.id)}
              className={cn(
                "w-full text-left px-4 py-3 transition-colors hover:bg-bg-hover",
                selectedId === decision.id && "bg-bg-elevated border-l-2 border-l-primary"
              )}
            >
              <div className="flex items-center gap-2">
                {/* Timestamp */}
                <span className="font-mono text-[9px] text-text-tertiary">{timeStr}</span>

                {/* Action badge */}
                <span
                  className={cn(
                    "rounded border px-1.5 py-0.5 font-mono text-[8px] tracking-wider",
                    actionColors[decision.action] ?? actionColors.HOLD
                  )}
                >
                  {decision.action}
                </span>

                {/* Source badge */}
                <span
                  className={cn(
                    "rounded border px-1.5 py-0.5 font-mono text-[8px] tracking-wider",
                    decision.source === "claude"
                      ? "border-cyan/20 bg-cyan-muted text-cyan"
                      : "border-primary/20 bg-primary-muted text-primary"
                  )}
                >
                  {decision.source === "claude" ? "CLAUDE" : "CB:"}
                </span>

                {/* Order count */}
                {decision.orders.length > 0 && (
                  <span className="font-mono text-[8px] text-text-muted">
                    {decision.orders.length} order{decision.orders.length !== 1 ? "s" : ""}
                  </span>
                )}
              </div>

              {/* Summary */}
              <p className="mt-1 text-xs font-medium text-text-primary">{decision.summary}</p>

              {/* Execution status */}
              <span className={cn("mt-0.5 font-mono text-[8px]", execStatus.color)}>
                {execStatus.label}
              </span>
            </motion.button>
          );
        })}

        {decisions.length === 0 && (
          <div className="px-4 py-8 text-center">
            <span className="text-xs text-text-tertiary">No decisions match filters</span>
          </div>
        )}
      </div>
    </div>
  );
}
