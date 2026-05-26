/**
 * Minimal styled table primitive shared by the data pages.
 *
 * Server-rendered. No state, no client JS. Each insight page builds its rows
 * inline (per-row formatting differs enough that a generic prop API would
 * lose more than it saves on ~5 pages).
 */

import type { CSSProperties, ReactNode } from "react";

export const tableStyles: Record<string, CSSProperties> = {
  wrap: { overflowX: "auto", marginTop: "1rem" },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "0.875rem",
    fontVariantNumeric: "tabular-nums",
  },
  th: {
    textAlign: "left",
    padding: "0.5rem 0.75rem",
    borderBottom: "1px solid #1f2937",
    color: "#9ca3af",
    fontWeight: 600,
    fontSize: "0.75rem",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    whiteSpace: "nowrap",
  },
  td: {
    padding: "0.5rem 0.75rem",
    borderBottom: "1px solid #1f2937",
    verticalAlign: "top",
  },
  empty: {
    padding: "2rem",
    textAlign: "center",
    color: "#6b7280",
    fontStyle: "italic",
  },
  h1: { fontSize: "1.5rem", fontWeight: 600, margin: 0 },
  lede: { color: "#9ca3af", marginTop: "0.5rem", maxWidth: "44rem" },
  toolbar: {
    display: "flex",
    gap: "1rem",
    alignItems: "center",
    marginTop: "1.5rem",
    flexWrap: "wrap",
  },
  pill: {
    display: "inline-block",
    padding: "0.125rem 0.5rem",
    borderRadius: "9999px",
    fontSize: "0.75rem",
    fontWeight: 600,
  },
  errorBox: {
    marginTop: "1.5rem",
    padding: "1rem",
    background: "#1f1010",
    border: "1px solid #7f1d1d",
    borderRadius: "0.375rem",
    color: "#fecaca",
    fontSize: "0.875rem",
  },
};

export function ErrorBox({ children }: { children: ReactNode }) {
  return <div style={tableStyles.errorBox}>{children}</div>;
}

export function EmptyRow({ cols, message }: { cols: number; message: string }) {
  return (
    <tr>
      <td colSpan={cols} style={tableStyles.empty}>
        {message}
      </td>
    </tr>
  );
}

export function formatDate(d: Date | null | undefined): string {
  if (!d) return "—";
  // ISO truncated to seconds, no millis, no TZ — keep it readable in a table.
  return new Date(d).toISOString().replace("T", " ").slice(0, 19) + "Z";
}

export function formatUsd(raw: string | number | null | undefined): string {
  if (raw === null || raw === undefined) return "—";
  const n = typeof raw === "string" ? parseFloat(raw) : raw;
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPct(raw: string | number | null | undefined): string {
  if (raw === null || raw === undefined) return "—";
  const n = typeof raw === "string" ? parseFloat(raw) : raw;
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(2)}%`;
}
