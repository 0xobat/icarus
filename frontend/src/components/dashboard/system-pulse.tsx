"use client";

import { useEffect, useRef } from "react";

export function SystemPulse() {
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

    const draw = () => {
      const w = canvas.width / dpr;
      const h = canvas.height / dpr;

      ctx.clearRect(0, 0, w, h);

      // Draw the pulse line
      ctx.beginPath();
      ctx.strokeStyle = "rgba(224, 122, 95, 0.3)";
      ctx.lineWidth = 1;

      for (let x = 0; x < w; x++) {
        const t = (x + offset) * 0.02;
        // Heartbeat-like waveform
        const beat = Math.exp(-((t % 6) * (t % 6))) * 8;
        const secondBeat = Math.exp(-(((t % 6) - 0.8) * ((t % 6) - 0.8))) * 4;
        const noise = Math.sin(t * 3) * 0.5;
        const y = h / 2 - beat - secondBeat - noise;

        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      // Glow line on top
      ctx.beginPath();
      ctx.strokeStyle = "rgba(224, 122, 95, 0.08)";
      ctx.lineWidth = 4;

      for (let x = 0; x < w; x++) {
        const t = (x + offset) * 0.02;
        const beat = Math.exp(-((t % 6) * (t % 6))) * 8;
        const secondBeat = Math.exp(-(((t % 6) - 0.8) * ((t % 6) - 0.8))) * 4;
        const noise = Math.sin(t * 3) * 0.5;
        const y = h / 2 - beat - secondBeat - noise;

        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      offset += 0.6;
      animFrame = requestAnimationFrame(draw);
    };

    draw();

    const obs = new ResizeObserver(resize);
    obs.observe(canvas);

    return () => {
      cancelAnimationFrame(animFrame);
      obs.disconnect();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="h-6 w-full"
      style={{ imageRendering: "auto" }}
    />
  );
}
