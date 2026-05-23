"use client";

import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { ExternalLink, CheckCircle, XCircle, Clock } from "lucide-react";
import type { DecisionDetail } from "@/lib/types";

interface ExecutionRow {
  status: "success" | "pending" | "failed";
  timestamp: string;
  tx_hash: string;
  type: string;
  strategy_id: string;
  description: string;
  value: number;
  gas_cost_usd: number;
}

interface ExecutionTableProps {
  decisions: DecisionDetail[];
}

const statusIcon: Record<string, React.ReactNode> = {
  success: <CheckCircle className="h-3 w-3 text-success" />,
  pending: <Clock className="h-3 w-3 text-warning" />,
  failed: <XCircle className="h-3 w-3 text-danger" />,
};

const typeColors: Record<string, string> = {
  entry: "bg-primary-muted text-primary border-primary/20",
  exit: "bg-danger-muted text-danger border-danger/20",
  harvest: "bg-success-muted text-success border-success/20",
  rebalance: "bg-warning-muted text-warning border-warning/20",
};

function flattenExecutions(decisions: DecisionDetail[]): ExecutionRow[] {
  const rows: ExecutionRow[] = [];
  for (const decision of decisions) {
    for (let i = 0; i < decision.executions.length; i++) {
      const exec = decision.executions[i];
      const order = decision.orders[i];
      rows.push({
        status: exec.status,
        timestamp: decision.timestamp,
        tx_hash: exec.tx_hash,
        type: order?.action ?? decision.action.toLowerCase(),
        strategy_id:
          decision.trigger_reports.length > 0
            ? decision.trigger_reports[0].strategy_id
            : "—",
        description: decision.summary,
        value: exec.value,
        gas_cost_usd: exec.gas_cost_usd,
      });
    }
  }
  return rows;
}

export function ExecutionTable({ decisions }: ExecutionTableProps) {
  const rows = flattenExecutions(decisions);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.1 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-primary">
          Execution History
        </span>
        <span className="ml-2 font-mono text-[9px] text-text-secondary">
          {rows.length} transaction{rows.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Header row */}
      <div className="grid grid-cols-[24px_1fr_1fr_80px_80px_2fr_80px_64px] gap-2 border-b border-border-subtle px-4 py-2">
        {["", "Timestamp", "TX Hash", "Type", "Strategy", "Description", "Value", "Gas"].map(
          (h) => (
            <span
              key={h || "status"}
              className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary"
            >
              {h}
            </span>
          )
        )}
      </div>

      {/* Rows */}
      <div className="divide-y divide-border-subtle">
        {rows.map((row, i) => {
          const ts = new Date(row.timestamp);
          const timeStr = ts.toLocaleString([], {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          });
          const truncHash =
            row.tx_hash.length > 16
              ? `${row.tx_hash.slice(0, 8)}...${row.tx_hash.slice(-4)}`
              : row.tx_hash;

          return (
            <div
              key={`${row.tx_hash}-${i}`}
              className={cn(
                "grid grid-cols-[24px_1fr_1fr_80px_80px_2fr_80px_64px] gap-2 px-4 py-2.5 transition-colors hover:bg-bg-hover",
                row.status === "pending" && "border-l-2 border-l-primary bg-primary-ghost"
              )}
            >
              {/* Status */}
              <div className="flex items-center">{statusIcon[row.status]}</div>

              {/* Timestamp */}
              <span className="font-mono text-[10px] text-text-secondary">{timeStr}</span>

              {/* TX Hash */}
              <div className="flex items-center gap-1">
                <span className="font-mono text-[10px] text-text-primary">{truncHash}</span>
                <a
                  href="#"
                  className="text-text-tertiary hover:text-primary transition-colors"
                  title="View on BaseScan"
                >
                  <ExternalLink className="h-2.5 w-2.5" />
                </a>
              </div>

              {/* Type badge */}
              <span
                className={cn(
                  "inline-flex w-fit items-center rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider",
                  typeColors[row.type] ?? "bg-bg-elevated text-text-secondary border-border-default"
                )}
              >
                {row.type}
              </span>

              {/* Strategy */}
              <span className="font-mono text-[10px] text-primary">{row.strategy_id}</span>

              {/* Description */}
              <span className="truncate text-[10px] text-text-primary">{row.description}</span>

              {/* Value */}
              <span className="font-mono text-[10px] text-text-primary">
                ${row.value.toLocaleString()}
              </span>

              {/* Gas */}
              <span className="font-mono text-[9px] text-text-tertiary">
                ${row.gas_cost_usd.toFixed(2)}
              </span>
            </div>
          );
        })}

        {rows.length === 0 && (
          <div className="px-4 py-8 text-center">
            <span className="text-xs text-text-tertiary">No executions yet</span>
          </div>
        )}
      </div>
    </motion.div>
  );
}
