"use client";

import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { ExternalLink, CheckCircle, XCircle, Clock } from "lucide-react";
import type { DecisionDetail as DecisionDetailType } from "@/lib/types";

interface DecisionDetailProps {
  decision: DecisionDetailType | null;
}

const statusIcon: Record<string, React.ReactNode> = {
  success: <CheckCircle className="h-3 w-3 text-success" />,
  pending: <Clock className="h-3 w-3 text-warning" />,
  failed: <XCircle className="h-3 w-3 text-danger" />,
};

export function DecisionDetailPanel({ decision }: DecisionDetailProps) {
  if (!decision) {
    return (
      <div className="sticky top-0 flex h-64 items-center justify-center rounded-lg border border-border-subtle bg-bg-surface">
        <span className="text-xs text-text-tertiary">Select a decision to view details</span>
      </div>
    );
  }

  const isCB = decision.source === "circuit_breaker";
  const ts = new Date(decision.timestamp);
  const timeStr = ts.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  return (
    <motion.div
      key={decision.id}
      initial={{ opacity: 0, x: 12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3 }}
      className="sticky top-0 max-h-screen overflow-y-auto rounded-lg border border-border-subtle bg-bg-surface"
    >
      {/* Header */}
      <div className="border-b border-border-subtle px-4 py-3">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[10px] font-semibold text-primary">{decision.id}</span>
          <span className="font-mono text-[9px] text-text-secondary">{timeStr}</span>
        </div>
        <p className="mt-1 text-sm font-medium text-text-primary">{decision.summary}</p>
      </div>

      {/* Section 1: Trigger */}
      <div className="border-b border-border-subtle px-4 py-3">
        <h4 className="font-display text-[10px] font-bold uppercase tracking-widest text-text-secondary">
          Trigger
        </h4>
        <div className="mt-2 space-y-1.5">
          {decision.trigger_reports.map((report) => (
            <div key={report.strategy_id} className="flex items-start gap-2">
              <span className="font-mono text-[10px] font-semibold text-primary">
                {report.strategy_id}
              </span>
              <div className="flex flex-wrap gap-1">
                {report.signals.map((signal) => (
                  <span
                    key={signal}
                    className="rounded bg-bg-elevated px-1.5 py-0.5 font-mono text-[9px] text-text-secondary"
                  >
                    {signal}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Section 2: Claude's Reasoning (or CB trigger details) */}
      {isCB ? (
        <div className="border-b border-border-subtle px-4 py-3">
          <h4 className="font-display text-[10px] font-bold uppercase tracking-widest text-danger">
            Circuit Breaker Trigger
          </h4>
          <p className="mt-2 text-xs text-text-primary">{decision.reasoning}</p>
        </div>
      ) : (
        <div className="border-b border-l-2 border-b-border-subtle border-l-cyan/30 px-4 py-3">
          <h4 className="font-display text-[10px] font-bold uppercase tracking-widest text-cyan">
            Claude&apos;s Reasoning
          </h4>
          <p className="mt-2 text-xs leading-relaxed text-text-primary">{decision.reasoning}</p>
        </div>
      )}

      {/* Section 3: Orders Emitted */}
      <div className="border-b border-border-subtle px-4 py-3">
        <h4 className="font-display text-[10px] font-bold uppercase tracking-widest text-text-secondary">
          Orders Emitted
        </h4>
        {decision.orders.length === 0 ? (
          <p className="mt-2 text-xs text-text-tertiary">No orders emitted</p>
        ) : (
          <div className="mt-2 space-y-2">
            {decision.orders.map((order, i) => (
              <div
                key={i}
                className="rounded border border-border-subtle bg-bg-elevated px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span className="rounded bg-primary-muted px-1.5 py-0.5 font-mono text-[9px] uppercase text-primary">
                    {order.action}
                  </span>
                  <span className="font-mono text-[10px] text-text-primary">
                    {order.protocol}
                  </span>
                  <span className="font-mono text-[10px] text-text-secondary">{order.asset}</span>
                </div>
                <div className="mt-1 font-mono text-[10px] text-text-secondary">
                  ${order.amount.toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Section 4: Verification Gate (not shown for CB) */}
      {!isCB && (
        <div className="border-b border-border-subtle px-4 py-3">
          <h4 className="font-display text-[10px] font-bold uppercase tracking-widest text-text-secondary">
            Verification Gate
          </h4>
          <div className="mt-2 flex items-center gap-2">
            {decision.verification.passed ? (
              <CheckCircle className="h-3.5 w-3.5 text-success" />
            ) : (
              <XCircle className="h-3.5 w-3.5 text-danger" />
            )}
            <span
              className={cn(
                "font-mono text-[10px] font-semibold",
                decision.verification.passed ? "text-success" : "text-danger"
              )}
            >
              {decision.verification.passed ? "PASSED" : "REJECTED"}
            </span>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {decision.verification.checks.map((check) => (
              <span
                key={check}
                className="rounded bg-success-muted px-1.5 py-0.5 font-mono text-[9px] text-success"
              >
                {check}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Section 5: Execution Results */}
      <div className="px-4 py-3">
        <h4 className="font-display text-[10px] font-bold uppercase tracking-widest text-text-secondary">
          Execution Results
        </h4>
        {decision.executions.length === 0 ? (
          <p className="mt-2 text-xs text-text-tertiary">No executions</p>
        ) : (
          <div className="mt-2 space-y-2">
            {decision.executions.map((exec, i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded border border-border-subtle bg-bg-elevated px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  {statusIcon[exec.status]}
                  <span className="font-mono text-[10px] text-text-primary">
                    {exec.tx_hash.length > 16
                      ? `${exec.tx_hash.slice(0, 8)}...${exec.tx_hash.slice(-4)}`
                      : exec.tx_hash}
                  </span>
                  <a
                    href="#"
                    className="text-text-tertiary hover:text-primary transition-colors"
                    title="View on BaseScan"
                  >
                    <ExternalLink className="h-2.5 w-2.5" />
                  </a>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-[10px] text-text-secondary">
                    ${exec.value.toLocaleString()}
                  </span>
                  <span className="font-mono text-[9px] text-text-tertiary">
                    gas: ${exec.gas_cost_usd.toFixed(2)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}
