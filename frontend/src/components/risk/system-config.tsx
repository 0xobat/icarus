"use client";

import { motion } from "motion/react";

interface ConfigEntry {
  label: string;
  value: string;
}

const configSections: { title: string; entries: ConfigEntry[] }[] = [
  {
    title: "Network",
    entries: [
      { label: "Chain ID", value: "8453 (Base)" },
      { label: "Safe Address", value: "0x1a2b...3c4d" },
      { label: "RPC Provider", value: "Alchemy WebSocket" },
    ],
  },
  {
    title: "Risk Thresholds",
    entries: [
      { label: "Max Drawdown", value: "20%" },
      { label: "Max Position Loss", value: "10%" },
      { label: "Gas Spike Multiplier", value: "3x 24h avg" },
      { label: "TX Failure Rate Limit", value: "3/hour" },
      { label: "Protocol TVL Drop", value: "30%/24h" },
    ],
  },
  {
    title: "Strategy Limits",
    entries: [
      { label: "LEND-001 Max Allocation", value: "70%" },
      { label: "LP-001 Max Allocation", value: "50%" },
      { label: "Min Liquid Reserve", value: "10%" },
    ],
  },
  {
    title: "Claude API",
    entries: [
      { label: "Model", value: "claude-sonnet-4-20250514" },
      { label: "Max Tokens", value: "4096" },
      { label: "Decision Budget", value: "100 calls/day" },
      { label: "Timeout", value: "30s" },
    ],
  },
  {
    title: "Eval Intervals",
    entries: [
      { label: "LEND-001", value: "60s" },
      { label: "LP-001", value: "45s" },
    ],
  },
];

export function SystemConfig() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.15 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-primary">
          System Configuration
        </span>
        <span className="ml-2 font-mono text-[8px] text-text-muted">(read-only)</span>
      </div>

      <div className="divide-y divide-border-subtle">
        {configSections.map((section) => (
          <div key={section.title} className="px-4 py-3">
            <span className="font-mono text-[8px] font-semibold uppercase tracking-widest text-text-tertiary">
              {section.title}
            </span>
            <div className="mt-2 space-y-1.5">
              {section.entries.map((entry) => (
                <div key={entry.label} className="flex items-center justify-between">
                  <span className="text-[10px] text-text-secondary">{entry.label}</span>
                  <span className="font-mono text-[10px] text-text-primary">{entry.value}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}
