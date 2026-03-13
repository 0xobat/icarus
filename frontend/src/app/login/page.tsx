"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      if (!res.ok) {
        const data = await res.json();
        setError(data.error || "Login failed");
        return;
      }

      router.push("/");
    } catch {
      setError("Network error — unable to reach server");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center"
      style={{ background: "var(--bg-root)" }}
    >
      <div className="grid-bg fixed inset-0 opacity-30" />

      <div
        className="relative z-10 w-full max-w-sm rounded-lg border p-8"
        style={{
          background: "var(--bg-surface)",
          borderColor: "var(--border-default)",
          boxShadow: "var(--glow-primary)",
        }}
      >
        {/* Header */}
        <div className="mb-8 text-center">
          <h1
            className="text-2xl font-bold tracking-wider"
            style={{
              fontFamily: "var(--font-display), system-ui, sans-serif",
              color: "var(--primary)",
            }}
          >
            ICARUS
          </h1>
          <p
            className="mt-1 text-xs uppercase tracking-widest"
            style={{ color: "var(--text-tertiary)" }}
          >
            Command Center
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label
              htmlFor="username"
              className="mb-1.5 block text-xs font-medium uppercase tracking-wider"
              style={{ color: "var(--text-secondary)" }}
            >
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoComplete="username"
              className="w-full rounded border px-3 py-2 text-sm outline-none transition-colors focus:border-transparent focus:ring-1"
              style={{
                background: "var(--bg-elevated)",
                borderColor: "var(--border-subtle)",
                color: "var(--text-primary)",
                fontFamily: "var(--font-mono), monospace",
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = "var(--primary)";
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = "var(--border-subtle)";
              }}
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="mb-1.5 block text-xs font-medium uppercase tracking-wider"
              style={{ color: "var(--text-secondary)" }}
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              className="w-full rounded border px-3 py-2 text-sm outline-none transition-colors focus:border-transparent focus:ring-1"
              style={{
                background: "var(--bg-elevated)",
                borderColor: "var(--border-subtle)",
                color: "var(--text-primary)",
                fontFamily: "var(--font-mono), monospace",
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = "var(--primary)";
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = "var(--border-subtle)";
              }}
            />
          </div>

          {error && (
            <div
              className="rounded border px-3 py-2 text-xs"
              style={{
                background: "var(--danger-muted)",
                borderColor: "rgba(248, 113, 113, 0.2)",
                color: "var(--danger)",
                fontFamily: "var(--font-mono), monospace",
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded py-2.5 text-sm font-semibold uppercase tracking-wider transition-all disabled:opacity-50"
            style={{
              background: loading ? "var(--primary-dim)" : "var(--primary)",
              color: "var(--bg-root)",
            }}
            onMouseEnter={(e) => {
              if (!loading) {
                e.currentTarget.style.boxShadow = "var(--glow-strong)";
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.boxShadow = "none";
            }}
          >
            {loading ? "Authenticating..." : "Login"}
          </button>
        </form>

        {/* Footer */}
        <p
          className="mt-6 text-center text-xs"
          style={{
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono), monospace",
          }}
        >
          Authorized operators only
        </p>
      </div>
    </div>
  );
}
