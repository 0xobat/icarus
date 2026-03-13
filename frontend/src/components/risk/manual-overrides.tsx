"use client";

import { useState } from "react";
import { Pause, Play, AlertTriangle, Zap } from "lucide-react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import type { StrategyData, CircuitBreaker } from "@/lib/types";

interface ManualOverridesProps {
  strategies: StrategyData[];
  breakers: CircuitBreaker[];
}

export function ManualOverrides({ strategies, breakers }: ManualOverridesProps) {
  const [holdMode, setHoldMode] = useState(false);
  const [holdConfirmOpen, setHoldConfirmOpen] = useState(false);
  const [strategyStates, setStrategyStates] = useState<Record<string, boolean>>(
    Object.fromEntries(strategies.map((s) => [s.id, s.status === "active"]))
  );
  const [cbConfirmOpen, setCbConfirmOpen] = useState<string | null>(null);

  const toggleStrategy = (id: string) => {
    setStrategyStates((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-primary">
          Manual Overrides
        </span>
      </div>

      <div className="grid grid-cols-3 divide-x divide-border-subtle">
        {/* Hold Mode Toggle */}
        <div className="px-4 py-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-3.5 w-3.5 text-warning" />
            <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-secondary">
              Hold Mode
            </span>
          </div>
          <p className="mt-1 text-[10px] text-text-secondary">
            Halt all new positions. Circuit breakers remain active.
          </p>
          <button
            onClick={() => setHoldConfirmOpen(true)}
            className={cn(
              "mt-3 flex items-center gap-2 rounded-md px-4 py-2 font-mono text-xs font-semibold transition-colors",
              holdMode
                ? "bg-warning text-black hover:bg-warning/80"
                : "border border-border-subtle bg-bg-elevated text-text-secondary hover:bg-bg-hover"
            )}
          >
            <div
              className={cn(
                "h-2 w-2 rounded-full",
                holdMode ? "bg-black animate-pulse-glow" : "bg-text-muted"
              )}
            />
            {holdMode ? "HOLD ACTIVE" : "ACTIVATE HOLD"}
          </button>

          <ConfirmDialog
            open={holdConfirmOpen}
            title={holdMode ? "Deactivate Hold Mode" : "Activate Hold Mode"}
            description={
              holdMode
                ? "This will allow the system to resume normal operations and open new positions."
                : "This will halt all new position entries. Existing positions will be maintained. Circuit breakers remain active."
            }
            confirmLabel={holdMode ? "Deactivate" : "Activate Hold"}
            onConfirm={() => {
              setHoldMode(!holdMode);
              setHoldConfirmOpen(false);
            }}
            onCancel={() => setHoldConfirmOpen(false)}
          />
        </div>

        {/* Strategy Controls */}
        <div className="px-4 py-4">
          <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-secondary">
            Strategy Controls
          </span>
          <div className="mt-3 space-y-2">
            {strategies.map((s) => (
              <div key={s.id} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div
                    className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      strategyStates[s.id] ? "bg-success" : "bg-text-muted"
                    )}
                  />
                  <span className="font-mono text-[10px] font-semibold text-primary">{s.id}</span>
                  <span className="text-[10px] text-text-secondary">{s.name}</span>
                </div>
                <button
                  onClick={() => toggleStrategy(s.id)}
                  className="flex h-[22px] w-[22px] items-center justify-center rounded border border-border-subtle text-text-tertiary hover:bg-bg-hover hover:text-primary transition-colors"
                >
                  {strategyStates[s.id] ? (
                    <Pause className="h-2.5 w-2.5" />
                  ) : (
                    <Play className="h-2.5 w-2.5" />
                  )}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Force Circuit Breaker */}
        <div className="px-4 py-4">
          <div className="flex items-center gap-2">
            <Zap className="h-3.5 w-3.5 text-danger" />
            <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-secondary">
              Force Circuit Breaker
            </span>
          </div>
          <div className="mt-3 space-y-2">
            {breakers.map((b) => (
              <div key={b.name} className="flex items-center justify-between">
                <span className="text-[10px] text-text-secondary">{b.name}</span>
                <button
                  onClick={() => setCbConfirmOpen(b.name)}
                  className="rounded border border-danger/30 bg-danger-muted px-2 py-0.5 font-mono text-[9px] text-danger hover:bg-danger/20 transition-colors"
                >
                  TRIGGER
                </button>
              </div>
            ))}
          </div>

          <ConfirmDialog
            open={cbConfirmOpen !== null}
            title={`Force Trigger: ${cbConfirmOpen}`}
            description={`This will manually trigger the ${cbConfirmOpen} circuit breaker. The system will take protective action including potential position unwinding.`}
            confirmLabel="Force Trigger"
            onConfirm={() => setCbConfirmOpen(null)}
            onCancel={() => setCbConfirmOpen(null)}
          />
        </div>
      </div>
    </motion.div>
  );
}
