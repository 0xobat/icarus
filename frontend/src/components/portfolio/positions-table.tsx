"use client";

import { useState } from "react";
import { motion } from "motion/react";
import type { Position } from "@/lib/types";
import { ChevronUp, ChevronDown } from "lucide-react";

type SortField =
  | "strategy_name"
  | "protocol"
  | "asset"
  | "amount"
  | "entry_price"
  | "current_value"
  | "unrealized_pnl"
  | "portfolio_pct";

type SortDir = "asc" | "desc";

const columns: { key: SortField; label: string; align?: "right" }[] = [
  { key: "strategy_name", label: "Strategy" },
  { key: "protocol", label: "Protocol" },
  { key: "asset", label: "Asset" },
  { key: "amount", label: "Amount", align: "right" },
  { key: "entry_price", label: "Entry Price", align: "right" },
  { key: "current_value", label: "Current Value", align: "right" },
  { key: "unrealized_pnl", label: "Unrealized P&L", align: "right" },
  { key: "portfolio_pct", label: "% of Portfolio", align: "right" },
];

function fmt(n: number, decimals = 2): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function PositionsTable({ positions }: { positions: Position[] }) {
  const [sortField, setSortField] = useState<SortField>("portfolio_pct");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const sorted = [...positions].sort((a, b) => {
    const aVal = a[sortField];
    const bVal = b[sortField];
    const cmp = typeof aVal === "string" ? aVal.localeCompare(bVal as string) : (aVal as number) - (bVal as number);
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.2, duration: 0.4 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      {/* Header */}
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
          Positions
        </span>
        <span className="ml-2 font-mono text-[10px] text-text-tertiary">
          {positions.length} open
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-border-subtle">
              {columns.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className={`cursor-pointer select-none px-4 py-2.5 font-mono text-[10px] font-medium tracking-wider text-text-tertiary uppercase transition-colors hover:text-text-secondary ${
                    col.align === "right" ? "text-right" : ""
                  }`}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.label}
                    {sortField === col.key && (
                      <span className="text-primary">
                        {sortDir === "asc" ? (
                          <ChevronUp className="h-3 w-3" />
                        ) : (
                          <ChevronDown className="h-3 w-3" />
                        )}
                      </span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((pos, i) => (
              <tr
                key={`${pos.strategy_id}-${pos.asset}`}
                className={`border-b border-border-subtle transition-colors hover:bg-bg-hover ${
                  i === sorted.length - 1 ? "border-b-0" : ""
                }`}
              >
                {/* Strategy */}
                <td className="px-4 py-2.5">
                  <div className="font-mono text-[11px] font-medium text-primary">
                    {pos.strategy_id}
                  </div>
                  <div className="font-mono text-[9px] text-text-tertiary">
                    {pos.strategy_name}
                  </div>
                </td>

                {/* Protocol */}
                <td className="px-4 py-2.5 font-mono text-[11px] text-text-secondary">
                  {pos.protocol}
                </td>

                {/* Asset */}
                <td className="px-4 py-2.5 font-mono text-[11px] text-text-primary">
                  {pos.asset}
                </td>

                {/* Amount */}
                <td className="px-4 py-2.5 text-right font-mono text-[11px] text-text-secondary">
                  {fmt(pos.amount, 0)}
                </td>

                {/* Entry Price */}
                <td className="px-4 py-2.5 text-right font-mono text-[11px] text-text-secondary">
                  ${fmt(pos.entry_price)}
                </td>

                {/* Current Value */}
                <td className="px-4 py-2.5 text-right font-mono text-[11px] font-medium text-text-primary">
                  ${fmt(pos.current_value, 0)}
                </td>

                {/* Unrealized P&L */}
                <td className="px-4 py-2.5 text-right">
                  <span
                    className={`font-mono text-[11px] font-medium ${
                      pos.unrealized_pnl >= 0 ? "text-success" : "text-danger"
                    }`}
                  >
                    {pos.unrealized_pnl >= 0 ? "+" : ""}${fmt(pos.unrealized_pnl, 0)}
                  </span>
                  <span
                    className={`ml-1 font-mono text-[9px] ${
                      pos.unrealized_pnl >= 0 ? "text-success/60" : "text-danger/60"
                    }`}
                  >
                    ({pos.unrealized_pnl >= 0 ? "+" : ""}{fmt(pos.unrealized_pnl_pct)}%)
                  </span>
                </td>

                {/* % of Portfolio */}
                <td className="px-4 py-2.5 text-right font-mono text-[11px] text-text-secondary">
                  {fmt(pos.portfolio_pct, 1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}
