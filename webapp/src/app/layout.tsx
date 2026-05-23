import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Icarus — Insight",
  description: "Read-only insight sidecar for the Icarus v2 strategy-lake trading bot",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', monospace",
          background: "#0b0d10",
          color: "#e5e7eb",
        }}
      >
        {children}
      </body>
    </html>
  );
}
