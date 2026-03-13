"use client";

import { motion } from "motion/react";
import { ManualOverrides } from "@/components/risk/manual-overrides";
import { CircuitBreakerCard } from "@/components/risk/circuit-breaker-card";
import { ExposureLimits } from "@/components/risk/exposure-limits";
import { InfraHealth } from "@/components/risk/infra-health";
import { SystemConfig } from "@/components/risk/system-config";
import {
  strategiesPanel,
  circuitBreakersData,
  exposureLimits,
  serviceHealth,
} from "@/lib/mock-data";

// Extend circuit breakers with mock history and trigger count for detailed cards
const breakersWithHistory = circuitBreakersData.map((cb) => ({
  ...cb,
  history: Array.from({ length: 24 }, (_, i) =>
    Math.max(0, cb.current + (Math.random() - 0.5) * cb.current * 0.8 - i * 0.05)
  ).reverse(),
  trigger_count: cb.last_triggered ? Math.floor(Math.random() * 8) + 1 : 0,
}));

export default function RiskPage() {
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
            RISK & OPERATIONS
          </h1>
          <div className="mt-1 flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-success animate-breathe" />
            <span className="font-mono text-[10px] text-success">SYSTEM NORMAL</span>
          </div>
        </div>
      </motion.div>

      {/* Manual Overrides — full width */}
      <ManualOverrides
        strategies={strategiesPanel.strategies}
        breakers={circuitBreakersData}
      />

      {/* Circuit Breakers (7fr) | Infra Health (5fr) */}
      <div className="grid grid-cols-[7fr_5fr] gap-3">
        <div>
          <div className="mb-2">
            <span className="font-display text-[11px] font-bold uppercase tracking-widest text-text-primary">
              Circuit Breakers
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {breakersWithHistory.map((breaker) => (
              <CircuitBreakerCard key={breaker.name} breaker={breaker} />
            ))}
          </div>
        </div>

        <InfraHealth services={serviceHealth} />
      </div>

      {/* Exposure Limits (7fr) | System Config (5fr) */}
      <div className="grid grid-cols-[7fr_5fr] gap-3">
        <ExposureLimits limits={exposureLimits} />
        <SystemConfig />
      </div>
    </div>
  );
}
