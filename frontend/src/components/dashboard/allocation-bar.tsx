"use client";

import type { StrategiesPanelData } from "@/lib/types";

const rustShades = ["#E07A5F", "#c4654b", "#a85238", "#8c4028"];

export function AllocationBar({ data }: { data: StrategiesPanelData }) {
  const activeStrategies = data.strategies.filter((s) => s.status === "active");

  return (
    <div className="px-4 py-3 border-b border-border-subtle">
      {/* Bar */}
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-bg-elevated">
        {activeStrategies.map((s, i) => (
          <div
            key={s.id}
            style={{
              width: `${s.allocation_pct}%`,
              backgroundColor: rustShades[i % rustShades.length],
            }}
            className="h-full transition-all duration-500"
          />
        ))}
        <div
          style={{ width: `${data.reserve.pct}%` }}
          className="h-full bg-bg-active"
        />
      </div>
      {/* Legend */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {activeStrategies.map((s, i) => (
          <div key={s.id} className="flex items-center gap-1.5">
            <div
              className="h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: rustShades[i % rustShades.length] }}
            />
            <span className="font-mono text-[9px] text-text-secondary">
              {s.id} {s.allocation_pct}%
            </span>
          </div>
        ))}
        <div className="flex items-center gap-1.5">
          <div className="h-1.5 w-1.5 rounded-full bg-bg-active" />
          <span className="font-mono text-[9px] text-text-secondary">
            Reserve {data.reserve.pct}%
          </span>
        </div>
      </div>
    </div>
  );
}
