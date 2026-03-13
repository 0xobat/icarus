"use client";

import { motion } from "motion/react";
import { TrendingUp, TrendingDown, Activity, Zap } from "lucide-react";
import Link from "next/link";
import { metricsData } from "@/lib/mock-data";

function Sparkline({ data, color }: { data: number[]; color: string }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 60;
  const h = 20;
  const points = data
    .map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="opacity-40">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

export function MetricsGrid() {
  return (
    <div className="grid grid-cols-[2fr_1fr_1fr_1fr] gap-3">
      {/* Portfolio Value — anchor card (2fr) */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0, duration: 0.4, ease: "easeOut" }}
        className="group rounded-lg border border-border-strong bg-bg-surface p-4 transition-all duration-300 hover:shadow-[var(--glow-primary)]"
      >
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] font-medium tracking-wider text-text-secondary uppercase">
            Portfolio Value
          </span>
          <div className="flex items-center gap-2">
            <Sparkline data={metricsData.portfolio_sparkline} color="#E07A5F" />
            <TrendingUp className="h-3.5 w-3.5 text-text-tertiary transition-colors group-hover:text-primary" strokeWidth={1.5} />
          </div>
        </div>
        <div className="mt-2 font-mono text-[26px] font-semibold tracking-tight text-text-primary">
          ${metricsData.portfolio_value.toLocaleString()}
        </div>
        <div className="mt-1 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-success">
              +{metricsData.portfolio_change_24h_pct}%
            </span>
            <span className="font-mono text-[10px] text-success/60">
              (+${metricsData.portfolio_change_24h_abs.toLocaleString()})
            </span>
            <span className="font-mono text-[10px] text-text-secondary">24h</span>
          </div>
          <Link href="/portfolio" className="font-mono text-[10px] text-primary hover:underline">
            &rarr; Portfolio
          </Link>
        </div>
      </motion.div>

      {/* Current Drawdown */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.08, duration: 0.4, ease: "easeOut" }}
        className="group rounded-lg border border-border-subtle bg-bg-surface p-4 transition-all duration-300 hover:border-border-default hover:shadow-[var(--glow-primary)]"
      >
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] font-medium tracking-wider text-text-secondary uppercase">
            Current Drawdown
          </span>
          <TrendingDown className="h-3.5 w-3.5 text-text-tertiary transition-colors group-hover:text-primary" strokeWidth={1.5} />
        </div>
        <div className="mt-2 font-mono text-2xl font-semibold tracking-tight text-text-primary">
          -{metricsData.drawdown_current}%
        </div>
        <div className="mt-1 flex items-center gap-2">
          <span className="font-mono text-xs text-text-secondary">
            Limit: {metricsData.drawdown_limit}%
          </span>
          <span className="font-mono text-[10px] text-text-secondary">from peak</span>
        </div>
      </motion.div>

      {/* Today's P&L */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.16, duration: 0.4, ease: "easeOut" }}
        className="group rounded-lg border border-border-subtle bg-bg-surface p-4 transition-all duration-300 hover:border-border-default hover:shadow-[var(--glow-primary)]"
      >
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] font-medium tracking-wider text-text-secondary uppercase">
            {"Today's P&L"}
          </span>
          <div className="flex items-center gap-2">
            <Sparkline data={metricsData.pnl_sparkline} color="#4ade80" />
            <Activity className="h-3.5 w-3.5 text-text-tertiary transition-colors group-hover:text-primary" strokeWidth={1.5} />
          </div>
        </div>
        <div className="mt-2 font-mono text-2xl font-semibold tracking-tight text-text-primary">
          +${metricsData.pnl_today.toLocaleString()}
        </div>
        <div className="mt-1 flex items-center gap-2">
          <span className="font-mono text-xs text-success">
            +{metricsData.pnl_today_pct}%
          </span>
        </div>
      </motion.div>

      {/* TX Success Rate */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.24, duration: 0.4, ease: "easeOut" }}
        className="group rounded-lg border border-border-subtle bg-bg-surface p-4 transition-all duration-300 hover:border-border-default hover:shadow-[var(--glow-primary)]"
      >
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] font-medium tracking-wider text-text-secondary uppercase">
            TX Success Rate
          </span>
          <Zap className="h-3.5 w-3.5 text-text-tertiary transition-colors group-hover:text-primary" strokeWidth={1.5} />
        </div>
        <div className="mt-2 font-mono text-2xl font-semibold tracking-tight text-text-primary">
          {metricsData.tx_success_rate}%
        </div>
        <div className="mt-1 flex items-center gap-2">
          <span className="font-mono text-xs text-success">
            {metricsData.tx_success_count}/{metricsData.tx_total_count}
          </span>
          <span className="font-mono text-[10px] text-text-secondary">last 24h</span>
        </div>
      </motion.div>
    </div>
  );
}
