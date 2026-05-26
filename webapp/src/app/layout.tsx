import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Icarus — Insight",
  description: "Read-only insight sidecar for the Icarus v2 strategy-lake trading bot",
};

const NAV_ITEMS: Array<{ href: "/" | "/lake" | "/templates" | "/decisions" | "/promotions"; label: string }> = [
  { href: "/", label: "Home" },
  { href: "/lake", label: "Lake" },
  { href: "/templates", label: "Templates" },
  { href: "/decisions", label: "Decisions" },
  { href: "/promotions", label: "Promotions" },
];

const styles = {
  body: {
    margin: 0,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
    background: "#0b0d10",
    color: "#e5e7eb",
    display: "flex",
    minHeight: "100vh",
  } as const,
  sidebar: {
    width: "12rem",
    padding: "1.5rem 1rem",
    background: "#111418",
    borderRight: "1px solid #1f2937",
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.25rem",
    flexShrink: 0,
  } as const,
  brand: {
    fontSize: "0.875rem",
    fontWeight: 700,
    letterSpacing: "0.05em",
    color: "#9ca3af",
    marginBottom: "1rem",
    textTransform: "uppercase" as const,
  } as const,
  link: {
    padding: "0.5rem 0.75rem",
    color: "#e5e7eb",
    textDecoration: "none",
    borderRadius: "0.25rem",
    fontSize: "0.9rem",
  } as const,
  main: {
    flex: 1,
    padding: "2rem 2.5rem",
    minWidth: 0,
  } as const,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body style={styles.body}>
        <nav style={styles.sidebar}>
          <div style={styles.brand}>Icarus · Insight</div>
          {NAV_ITEMS.map((item) => (
            <Link key={item.href} href={item.href} style={styles.link}>
              {item.label}
            </Link>
          ))}
        </nav>
        <main style={styles.main}>{children}</main>
      </body>
    </html>
  );
}
