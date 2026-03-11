"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import type { HoldModeData } from "@/lib/types";

export function HoldModeAlert({ data }: { data: HoldModeData }) {
  const [snoozed, setSnoozed] = useState(false);

  if (!data.active || snoozed) return null;

  const sinceDate = new Date(data.since);
  const sinceStr = sinceDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, height: 0 }}
        animate={{ opacity: 1, height: "auto" }}
        exit={{ opacity: 0, height: 0 }}
        className="rounded border-l-[3px] border-l-warning bg-warning-muted px-4 py-3"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 rounded-full bg-warning animate-pulse-glow" />
            <span className="font-display text-xs font-bold tracking-wide text-warning uppercase">
              HOLD MODE ACTIVE
            </span>
            <span className="text-xs text-text-secondary">{data.reason}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] text-text-tertiary">
              Since {sinceStr}
            </span>
            <button
              onClick={() => {
                setSnoozed(true);
                setTimeout(() => setSnoozed(false), 10 * 60 * 1000);
              }}
              className="rounded px-2 py-1 font-mono text-[9px] text-text-secondary border border-border-subtle hover:bg-bg-hover transition-colors"
            >
              SNOOZE 10m
            </button>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
