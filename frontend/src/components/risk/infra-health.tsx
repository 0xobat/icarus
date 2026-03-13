"use client";

import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import type { ServiceHealth } from "@/lib/types";

interface InfraHealthProps {
  services: ServiceHealth[];
}

const statusDotColor: Record<string, string> = {
  connected: "bg-success",
  disconnected: "bg-danger",
  degraded: "bg-warning",
};

const statusDotGlow: Record<string, string> = {
  connected: "",
  disconnected: "animate-pulse-glow",
  degraded: "animate-pulse-glow",
};

export function InfraHealth({ services }: InfraHealthProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.05 }}
      className="rounded-lg border border-border-subtle bg-bg-surface"
    >
      <div className="border-b border-border-subtle px-4 py-3">
        <span className="font-display text-[10px] font-bold uppercase tracking-widest text-text-primary">
          Infrastructure Health
        </span>
      </div>

      <div className="divide-y divide-border-subtle">
        {services.map((service) => {
          const heartbeatStr = new Date(service.last_heartbeat).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
          });

          return (
            <div
              key={service.name}
              className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-bg-hover"
            >
              <div className="flex items-center gap-3">
                {/* Status dot */}
                <div
                  className={cn(
                    "h-2 w-2 rounded-full",
                    statusDotColor[service.status],
                    statusDotGlow[service.status]
                  )}
                />
                {/* Name */}
                <div>
                  <span className="text-xs font-medium text-text-primary">{service.name}</span>
                  <p className="font-mono text-[9px] uppercase tracking-wider text-text-tertiary">
                    {service.status}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-4">
                {/* Latency */}
                <div className="text-right">
                  <span className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                    Latency
                  </span>
                  <p className="font-mono text-[10px] text-text-secondary">
                    {service.latency_ms !== null ? `${service.latency_ms}ms` : "—"}
                  </p>
                </div>

                {/* Last heartbeat */}
                <div className="text-right">
                  <span className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                    Heartbeat
                  </span>
                  <p className="font-mono text-[10px] text-text-secondary">{heartbeatStr}</p>
                </div>

                {/* Error count */}
                <div className="text-right">
                  <span className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                    Errors 24h
                  </span>
                  <p
                    className={cn(
                      "font-mono text-[10px]",
                      service.error_count_24h > 0 ? "text-danger" : "text-text-tertiary"
                    )}
                  >
                    {service.error_count_24h}
                  </p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}
