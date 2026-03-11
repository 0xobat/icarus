"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

const STRATEGIES = ["All", "LEND-001", "LP-001"];
const ACTIONS = ["ENTRY", "EXIT", "HARVEST", "REBALANCE", "HOLD"] as const;
const STATUSES = ["success", "pending", "failed"] as const;
const DATE_RANGES = ["Today", "7d", "30d"] as const;

export interface DecisionFilters {
  strategy: string;
  actions: string[];
  statuses: string[];
  dateRange: string;
}

interface DecisionFiltersProps {
  filters: DecisionFilters;
  onFilterChange: (filters: DecisionFilters) => void;
}

const actionColors: Record<string, string> = {
  ENTRY: "bg-primary-muted text-primary border-primary/20",
  EXIT: "bg-danger-muted text-danger border-danger/20",
  HARVEST: "bg-success-muted text-success border-success/20",
  REBALANCE: "bg-warning-muted text-warning border-warning/20",
  HOLD: "bg-bg-elevated text-text-secondary border-border-default",
};

const statusColors: Record<string, string> = {
  success: "bg-success-muted text-success border-success/20",
  pending: "bg-warning-muted text-warning border-warning/20",
  failed: "bg-danger-muted text-danger border-danger/20",
};

export function DecisionFiltersBar({ filters, onFilterChange }: DecisionFiltersProps) {
  const toggleAction = (action: string) => {
    const next = filters.actions.includes(action)
      ? filters.actions.filter((a) => a !== action)
      : [...filters.actions, action];
    onFilterChange({ ...filters, actions: next });
  };

  const toggleStatus = (status: string) => {
    const next = filters.statuses.includes(status)
      ? filters.statuses.filter((s) => s !== status)
      : [...filters.statuses, status];
    onFilterChange({ ...filters, statuses: next });
  };

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border-subtle bg-bg-surface px-4 py-3">
      {/* Strategy dropdown */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-[8px] uppercase tracking-widest text-text-tertiary">
          Strategy
        </span>
        <select
          value={filters.strategy}
          onChange={(e) => onFilterChange({ ...filters, strategy: e.target.value })}
          className="rounded border border-border-subtle bg-bg-elevated px-2 py-1 font-mono text-[10px] text-text-primary outline-none"
        >
          {STRATEGIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <div className="h-4 w-px bg-border-subtle" />

      {/* Action chips */}
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[8px] uppercase tracking-widest text-text-tertiary">
          Action
        </span>
        {ACTIONS.map((action) => (
          <button
            key={action}
            onClick={() => toggleAction(action)}
            className={cn(
              "rounded border px-2 py-0.5 font-mono text-[8px] tracking-wider transition-colors",
              filters.actions.includes(action)
                ? actionColors[action]
                : "border-border-subtle bg-transparent text-text-muted hover:text-text-tertiary"
            )}
          >
            {action}
          </button>
        ))}
      </div>

      <div className="h-4 w-px bg-border-subtle" />

      {/* Status chips */}
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[8px] uppercase tracking-widest text-text-tertiary">
          Status
        </span>
        {STATUSES.map((status) => (
          <button
            key={status}
            onClick={() => toggleStatus(status)}
            className={cn(
              "rounded border px-2 py-0.5 font-mono text-[8px] tracking-wider transition-colors capitalize",
              filters.statuses.includes(status)
                ? statusColors[status]
                : "border-border-subtle bg-transparent text-text-muted hover:text-text-tertiary"
            )}
          >
            {status}
          </button>
        ))}
      </div>

      <div className="h-4 w-px bg-border-subtle" />

      {/* Date range */}
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[8px] uppercase tracking-widest text-text-tertiary">
          Range
        </span>
        {DATE_RANGES.map((range) => (
          <button
            key={range}
            onClick={() => onFilterChange({ ...filters, dateRange: range })}
            className={cn(
              "rounded border px-2 py-0.5 font-mono text-[8px] tracking-wider transition-colors",
              filters.dateRange === range
                ? "border-primary/20 bg-primary-muted text-primary"
                : "border-border-subtle bg-transparent text-text-muted hover:text-text-tertiary"
            )}
          >
            {range}
          </button>
        ))}
      </div>
    </div>
  );
}
