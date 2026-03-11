"use client";

import { useEffect, useRef } from "react";
import type { DecisionLoopEvent } from "@/lib/types";

const EVENT_COLORS: Record<DecisionLoopEvent["type"], string> = {
  eval: "#E07A5F",       // rust ticks — small, regular
  claude_call: "#00B4D8", // cyan spikes — tall, rare
  tx_exec: "#4ade80",     // green ticks — downward, after cyan
};

const EVENT_HEIGHT: Record<DecisionLoopEvent["type"], { up: number; down: number }> = {
  eval: { up: 4, down: 0 },        // small upward tick
  claude_call: { up: 12, down: 0 }, // tall upward spike
  tx_exec: { up: 0, down: 8 },      // downward tick
};

export function DecisionLoopPulse({ events }: { events: DecisionLoopEvent[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animFrame: number;
    let offset = 0;
    const dpr = window.devicePixelRatio || 1;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
    };
    resize();

    // Map events to x positions within a 60-minute window
    const now = Date.now();
    const windowMs = 60 * 60 * 1000;

    const draw = () => {
      const w = canvas.width / dpr;
      const h = canvas.height / dpr;
      const mid = h / 2;
      ctx.clearRect(0, 0, w, h);

      // Baseline
      ctx.beginPath();
      ctx.strokeStyle = "rgba(224, 122, 95, 0.1)";
      ctx.lineWidth = 1;
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();

      // Draw events as ticks
      for (const evt of events) {
        const evtTime = new Date(evt.timestamp).getTime();
        const age = now - evtTime;
        if (age > windowMs || age < 0) continue;

        const x = ((windowMs - age) / windowMs) * w - offset % w;
        if (x < 0 || x > w) continue;

        const heights = EVENT_HEIGHT[evt.type];
        const color = EVENT_COLORS[evt.type];

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        if (heights.up > 0) {
          ctx.moveTo(x, mid);
          ctx.lineTo(x, mid - heights.up);
        }
        if (heights.down > 0) {
          ctx.moveTo(x, mid);
          ctx.lineTo(x, mid + heights.down);
        }
        ctx.stroke();
      }

      offset += 0.3;
      animFrame = requestAnimationFrame(draw);
    };

    draw();
    const obs = new ResizeObserver(resize);
    obs.observe(canvas);

    return () => {
      cancelAnimationFrame(animFrame);
      obs.disconnect();
    };
  }, [events]);

  return (
    <canvas ref={canvasRef} className="h-6 w-full" style={{ imageRendering: "auto" }} />
  );
}
