"use client";

import { ShieldCheck, Sparkles, Bell } from "lucide-react";

export function Topbar() {
  return (
    <header className="flex h-[38px] shrink-0 items-center justify-between border-b border-border-subtle bg-bg-surface px-6">
      {/* Left — branding */}
      <div className="flex items-center gap-3">
        <h1 className="font-display text-sm font-bold tracking-wide text-text-primary">
          ICARUS
        </h1>
        <span className="font-mono text-[10px] text-text-secondary tracking-wider">
          v4.2
        </span>
      </div>

      {/* Center — status badges */}
      <div className="flex items-center gap-3">
        <StatusBadge
          icon={<ShieldCheck className="h-3 w-3" />}
          label="SHIELDS"
          value="NOMINAL"
          color="primary"
        />
        <StatusBadge
          icon={<Sparkles className="h-3 w-3" />}
          label="CLAUDE"
          value="ONLINE"
          color="cyan"
        />
      </div>

      {/* Right — actions */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-bg-elevated px-2.5 py-1">
          <span className="font-mono text-[10px] text-text-tertiary">UPTIME</span>
          <span className="font-mono text-[10px] font-medium text-success">99.8%</span>
        </div>
        <button className="relative rounded-md p-1.5 text-text-tertiary transition-colors hover:bg-bg-hover hover:text-text-secondary">
          <Bell className="h-3.5 w-3.5" />
          <div className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber" />
        </button>
      </div>
    </header>
  );
}

function StatusBadge({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color: "primary" | "cyan" | "amber";
}) {
  const colorMap = {
    primary: "text-primary border-primary/20 bg-primary-ghost",
    cyan: "text-cyan border-cyan/20 bg-cyan-ghost",
    amber: "text-amber border-amber/20 bg-amber-muted/30",
  };

  return (
    <div
      className={`flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 ${colorMap[color]}`}
    >
      {icon}
      <span className="font-mono text-[10px] font-medium tracking-wider">
        {label}: {value}
      </span>
    </div>
  );
}
