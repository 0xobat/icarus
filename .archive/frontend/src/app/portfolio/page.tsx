"use client";

import { motion } from "motion/react";
import { PortfolioChart } from "@/components/dashboard/portfolio-chart";
import { PositionsTable } from "@/components/portfolio/positions-table";
import { AllocationView } from "@/components/portfolio/allocation-view";
import { PnlAttribution } from "@/components/portfolio/pnl-attribution";
import { ReserveStatus } from "@/components/portfolio/reserve-status";
import { SkeletonCard, SkeletonTable } from "@/components/shared/loading-skeleton";
import { StaleWrapper } from "@/components/shared/stale-indicator";
import { usePositions, useReserve } from "@/lib/hooks/use-portfolio";
import { useDashboardMetrics, useDashboardStrategies } from "@/lib/hooks/use-dashboard";

export default function PortfolioPage() {
  const { data: positions, isLoading: posLoading, stale: posStale } = usePositions();
  const { data: metricsData, isLoading: metLoading } = useDashboardMetrics();
  const { data: strategiesPanel, isLoading: stratLoading, stale: stratStale } = useDashboardStrategies();
  const { data: reserveData, isLoading: resLoading, stale: resStale } = useReserve();

  const isLoading = posLoading || metLoading || stratLoading || resLoading;
  const stale = posStale || stratStale || resStale;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1400px] space-y-4">
        <SkeletonCard />
        <SkeletonCard className="h-[280px]" />
        <div className="grid grid-cols-12 gap-3">
          <div className="col-span-7"><SkeletonTable /></div>
          <div className="col-span-5"><SkeletonCard /></div>
        </div>
      </div>
    );
  }

  return (
    <StaleWrapper isStale={stale}>
      <div className="mx-auto max-w-[1400px] space-y-4">
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="flex items-end justify-between"
        >
          <div>
            <h1 className="font-display text-2xl font-extrabold tracking-tight text-text-primary">
              PORTFOLIO
            </h1>
            <div className="mt-1 flex items-center gap-3">
              <span className="font-mono text-lg font-semibold text-text-primary">
                ${metricsData?.portfolio_value.toLocaleString() ?? "--"}
              </span>
              {metricsData && (
                <span className={`font-mono text-xs ${metricsData.portfolio_change_24h_pct >= 0 ? "text-success" : "text-danger"}`}>
                  {metricsData.portfolio_change_24h_pct >= 0 ? "+" : ""}
                  {metricsData.portfolio_change_24h_pct}% (24h)
                </span>
              )}
            </div>
          </div>
        </motion.div>

        {/* Chart */}
        <PortfolioChart height={280} showStrategyOverlay />

        <div className="grid grid-cols-12 gap-3">
          {/* Left column: Positions + P&L Attribution */}
          <div className="col-span-7 space-y-3">
            <PositionsTable positions={positions ?? []} />
            {strategiesPanel && (
              <PnlAttribution strategies={strategiesPanel.strategies} />
            )}
          </div>

          {/* Right column: Allocation + Reserve Status */}
          <div className="col-span-5 space-y-3">
            {strategiesPanel && (
              <AllocationView
                strategies={strategiesPanel.strategies}
                reserve={strategiesPanel.reserve}
              />
            )}
            {reserveData && <ReserveStatus data={reserveData} />}
          </div>
        </div>
      </div>
    </StaleWrapper>
  );
}
