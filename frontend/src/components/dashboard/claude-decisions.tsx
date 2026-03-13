"use client";

import { motion } from "motion/react";
import { Sparkles } from "lucide-react";
import Link from "next/link";
import { claudeDecisionsData } from "@/lib/mock-data";

const actionColors = {
  REBALANCE: "bg-warning-muted text-warning border-warning/20",
  ENTRY: "bg-primary-muted text-primary border-primary/20",
  EXIT: "bg-danger-muted text-danger border-danger/20",
  HOLD: "bg-bg-elevated text-text-secondary border-border-default",
};

export function ClaudeDecisions() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.55, duration: 0.4 }}
      className="rounded-lg border border-cyan/10 bg-bg-surface"
    >
      <div className="flex items-center justify-between border-b border-cyan/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-cyan" strokeWidth={1.5} />
          <span className="font-display text-xs font-bold tracking-wide text-text-primary uppercase">
            Claude Autopilot
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-text-secondary">
            {claudeDecisionsData.length} calls today
          </span>
          <Link href="/decisions" className="font-mono text-[10px] text-cyan hover:underline">
            FULL LOG &rarr;
          </Link>
        </div>
      </div>

      <div className="divide-y divide-border-subtle">
        {claudeDecisionsData.map((decision, i) => {
          const timeStr = new Date(decision.timestamp).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          });

          return (
            <motion.div
              key={decision.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.6 + i * 0.06, duration: 0.3 }}
              className="group px-4 py-3 transition-colors hover:bg-bg-hover cursor-pointer"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10px] text-text-secondary">
                    {timeStr}
                  </span>
                  <span
                    className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium tracking-wider ${
                      actionColors[decision.action] || actionColors.HOLD
                    }`}
                  >
                    {decision.action}
                  </span>
                  <span className="rounded bg-cyan-muted px-1.5 py-0.5 font-mono text-[10px] text-cyan">
                    {decision.order_count} order{decision.order_count !== 1 ? "s" : ""}
                  </span>
                </div>
                <span className="font-mono text-[10px] text-text-secondary opacity-0 transition-opacity group-hover:opacity-100">
                  {decision.id} &rarr;
                </span>
              </div>
              <p className="mt-1.5 text-xs font-medium text-text-primary">
                {decision.summary}
              </p>
              <p className="mt-1 text-[11px] text-text-secondary leading-relaxed">
                {decision.reasoning}
              </p>
            </motion.div>
          );
        })}
      </div>

      {/* Command input */}
      <div className="border-t border-cyan/10 px-4 py-3">
        <div className="flex items-center gap-2 rounded border border-cyan/30 bg-bg-root px-3 py-2">
          <input
            type="text"
            placeholder='Ask Claude... "pause LP-001" or "why did you rebalance?"'
            className="flex-1 bg-transparent font-mono text-xs text-text-primary placeholder:text-text-muted outline-none"
            disabled
          />
          <button className="font-mono text-xs text-cyan hover:text-cyan-dim transition-colors">
            &#x21B5;
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {["Pause all", "Force hold", "Explain last trade"].map((cmd) => (
            <button
              key={cmd}
              className="rounded border border-cyan/15 bg-cyan-ghost px-2.5 py-1 font-mono text-[9px] text-cyan hover:bg-cyan-muted transition-colors"
            >
              {cmd}
            </button>
          ))}
        </div>
      </div>
    </motion.div>
  );
}
