"use client";

import { motion } from "motion/react";
import { Check, Clock, ExternalLink } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { executionsData } from "@/lib/mock-data";

const typeStyles = {
  entry: "bg-primary-muted text-primary",
  exit: "bg-danger-muted text-danger",
  harvest: "bg-success-muted text-success",
  rebalance: "bg-amber-muted text-amber",
};

const statusIcon = {
  success: <Check className="h-3 w-3 text-success" />,
  pending: <Clock className="h-3 w-3 text-amber animate-pulse-glow" />,
  failed: <span className="h-3 w-3 text-danger">&#x2715;</span>,
};

export function ExecutionLog() {
  // Sort: pending first, then by original order
  const sorted = [...executionsData].sort((a, b) => {
    if (a.status === "pending" && b.status !== "pending") return -1;
    if (b.status === "pending" && a.status !== "pending") return 1;
    return 0;
  });

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.4, duration: 0.4 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
          Execution Log
        </span>
        <Link href="/decisions" className="font-mono text-[10px] text-primary hover:underline tracking-wider">
          VIEW ALL &rarr;
        </Link>
      </div>

      <div className="divide-y divide-border-subtle">
        {sorted.map((tx, i) => {
          const timeStr = new Date(tx.timestamp).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
          });

          return (
            <motion.div
              key={tx.id}
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.45 + i * 0.06, duration: 0.3 }}
              className={cn(
                "group flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-bg-hover",
                tx.status === "pending" && "border-l-2 border-l-primary bg-primary-ghost"
              )}
            >
              {/* Status */}
              <div className="flex h-5 w-5 items-center justify-center">
                {statusIcon[tx.status]}
              </div>

              {/* Time */}
              <span className="w-16 font-mono text-[10px] text-text-secondary">
                {timeStr}
              </span>

              {/* Type badge */}
              <span
                className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-medium tracking-wider uppercase ${typeStyles[tx.type]}`}
              >
                {tx.type}
              </span>

              {/* Strategy ID */}
              <span className="font-mono text-[10px] text-text-secondary">
                {tx.strategy_id}
              </span>

              {/* Description */}
              <span className="flex-1 truncate text-xs text-text-primary">
                {tx.description}
              </span>

              {/* Value */}
              <span
                className={`font-mono text-xs font-medium ${
                  tx.type === "entry" || tx.type === "harvest"
                    ? "text-success"
                    : "text-text-primary"
                }`}
              >
                {tx.type === "harvest" || tx.type === "entry" ? "+" : ""}${tx.value.toLocaleString()}
              </span>

              {/* Link */}
              <ExternalLink className="h-3 w-3 text-text-secondary opacity-0 transition-opacity group-hover:opacity-100" />
            </motion.div>
          );
        })}
      </div>
    </motion.div>
  );
}
