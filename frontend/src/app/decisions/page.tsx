"use client";

import { useState, useMemo } from "react";
import { motion } from "motion/react";
import { DecisionFiltersBar, type DecisionFilters } from "@/components/decisions/decision-filters";
import { DecisionTimeline } from "@/components/decisions/decision-timeline";
import { DecisionDetailPanel } from "@/components/decisions/decision-detail";
import { ExecutionTable } from "@/components/decisions/execution-table";
import { SkeletonCard, SkeletonTable } from "@/components/shared/loading-skeleton";
import { StaleWrapper } from "@/components/shared/stale-indicator";
import { useDecisions, useDecisionDetail } from "@/lib/hooks/use-decisions";

export default function DecisionsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filters, setFilters] = useState<DecisionFilters>({
    strategy: "All",
    actions: [],
    statuses: [],
    dateRange: "Today",
  });

  const { data: decisionsResult, isLoading, stale } = useDecisions({
    strategy: filters.strategy !== "All" ? filters.strategy : undefined,
  });
  const { data: selectedDecision } = useDecisionDetail(selectedId);

  const decisionDetails = decisionsResult?.data ?? [];

  const filteredDecisions = useMemo(() => {
    return decisionDetails.filter((d) => {
      // Strategy filter (already applied at API level, but also filter locally for actions/statuses)
      if (filters.strategy !== "All") {
        const hasStrategy = d.trigger_reports.some(
          (r) => r.strategy_id === filters.strategy
        );
        if (!hasStrategy) return false;
      }
      // Action filter
      if (filters.actions.length > 0 && !filters.actions.includes(d.action)) {
        return false;
      }
      // Status filter
      if (filters.statuses.length > 0) {
        const execStatuses = d.executions.map((e) => e.status);
        const hasMatchingStatus = filters.statuses.some(
          (s) => execStatuses.includes(s as "success" | "pending" | "failed") || (s === "success" && d.executions.length === 0 && d.action === "HOLD")
        );
        if (!hasMatchingStatus) return false;
      }
      return true;
    });
  }, [decisionDetails, filters]);

  const totalCallsToday = decisionDetails.length;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1400px] space-y-4">
        <SkeletonCard />
        <SkeletonCard />
        <div className="grid grid-cols-[7fr_5fr] gap-3">
          <SkeletonTable />
          <SkeletonCard />
        </div>
      </div>
    );
  }

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
              DECISIONS
            </h1>
            <div className="mt-1 flex items-center gap-3">
              <span className="font-mono text-[10px] text-text-secondary">
                {totalCallsToday} call{totalCallsToday !== 1 ? "s" : ""} today
              </span>
            </div>
          </div>
        </motion.div>

        {/* Filters */}
        <DecisionFiltersBar filters={filters} onFilterChange={setFilters} />

        {/* Two-column layout: Timeline + Detail */}
        <div className="grid grid-cols-[7fr_5fr] gap-3">
          <DecisionTimeline
            decisions={filteredDecisions}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <DecisionDetailPanel decision={selectedDecision ?? null} />
        </div>

        {/* Full-width Execution Table */}
        <ExecutionTable decisions={filteredDecisions} />
      </div>
    </StaleWrapper>
  );
}
