"use client";

import { motion } from "motion/react";
import { TrendingUp, TrendingDown, Activity, Zap } from "lucide-react";
import { metrics } from "@/lib/mock-data";

const cards = [
  {
    label: "Portfolio Value",
    value: `$${metrics.portfolioValue.toLocaleString()}`,
    change: `+${metrics.portfolioChange24h}%`,
    changePositive: true,
    sublabel: "24h",
    icon: TrendingUp,
  },
  {
    label: "Current Drawdown",
    value: `${metrics.drawdown}%`,
    change: `Limit: ${metrics.drawdownLimit}%`,
    changePositive: false,
    sublabel: "from peak",
    icon: TrendingDown,
  },
  {
    label: "Today's P&L",
    value: `+$${metrics.todayPnl.toLocaleString()}`,
    change: `+${metrics.pnlChange}%`,
    changePositive: true,
    sublabel: "",
    icon: Activity,
  },
  {
    label: "TX Success Rate",
    value: `${metrics.txSuccess}%`,
    change: `${metrics.txSuccessCount}/${metrics.txTotal}`,
    changePositive: true,
    sublabel: "last 24h",
    icon: Zap,
  },
];

export function MetricsGrid() {
  return (
    <div className="grid grid-cols-4 gap-3">
      {cards.map((card, i) => (
        <motion.div
          key={card.label}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.08, duration: 0.4, ease: "easeOut" }}
          className="group rounded-lg border border-border-subtle bg-bg-surface p-4 transition-all duration-300 hover:border-border-default hover:shadow-[var(--glow-primary)]"
        >
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] font-medium tracking-wider text-text-tertiary uppercase">
              {card.label}
            </span>
            <card.icon className="h-3.5 w-3.5 text-text-tertiary transition-colors group-hover:text-primary" strokeWidth={1.5} />
          </div>
          <div className="mt-2 font-mono text-2xl font-semibold tracking-tight text-text-primary">
            {card.value}
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={`font-mono text-xs ${
                card.changePositive ? "text-success" : "text-text-secondary"
              }`}
            >
              {card.change}
            </span>
            {card.sublabel && (
              <span className="font-mono text-[10px] text-text-tertiary">{card.sublabel}</span>
            )}
          </div>
        </motion.div>
      ))}
    </div>
  );
}
