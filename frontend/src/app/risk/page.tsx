"use client";

import { motion } from "motion/react";
import { ManualOverrides } from "@/components/risk/manual-overrides";
import { CircuitBreakerCard } from "@/components/risk/circuit-breaker-card";
import { ExposureLimits } from "@/components/risk/exposure-limits";
import { InfraHealth } from "@/components/risk/infra-health";
import { SystemConfig } from "@/components/risk/system-config";
import { SkeletonCard } from "@/components/shared/loading-skeleton";
import { StaleWrapper } from "@/components/shared/stale-indicator";
import { useDashboardStrategies, useDashboardBreakers } from "@/lib/hooks/use-dashboard";
import { useExposure, useSystemHealth } from "@/lib/hooks/use-risk";

export default function RiskPage() {
  const { data: strategiesPanel, isLoading: stratLoading } = useDashboardStrategies();
  const { data: circuitBreakersData, isLoading: cbLoading, stale: cbStale } = useDashboardBreakers();
  const { data: exposureLimitsData, isLoading: expLoading, stale: expStale } = useExposure();
  const { data: serviceHealthData, isLoading: healthLoading, stale: healthStale } = useSystemHealth();

  const isLoading = stratLoading || cbLoading || expLoading || healthLoading;
  const stale = cbStale || expStale || healthStale;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1400px] space-y-4">
        <SkeletonCard />
        <SkeletonCard />
        <div className="grid grid-cols-[7fr_5fr] gap-3">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    );
  }

  // Pass breakers with empty history — no fabricated data
  const breakersWithHistory = (circuitBreakersData ?? []).map((cb) => ({
    ...cb,
    history: [] as number[],
    trigger_count: 0,
  }));

  return (
    <StaleWrapper isStale={stale}>
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
        {strategiesPanel && circuitBreakersData && (
          <ManualOverrides
            strategies={strategiesPanel.strategies}
            breakers={circuitBreakersData}
          />
        )}

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

          {serviceHealthData && <InfraHealth services={serviceHealthData} />}
        </div>

        {/* Exposure Limits (7fr) | System Config (5fr) */}
        <div className="grid grid-cols-[7fr_5fr] gap-3">
          {exposureLimitsData && <ExposureLimits limits={exposureLimitsData} />}
          <SystemConfig />
        </div>
      </div>
    </StaleWrapper>
  );
}
