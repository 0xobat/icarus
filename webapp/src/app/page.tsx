/**
 * Webapp landing page — entry point for Saturday-morning operator pulls.
 *
 * Per blueprint §"Curation cluster components — Webapp insight sidecar"
 * (line 329 onward): Discord is the push channel for alerts + approvals;
 * this app is the pull channel for investigation. The four cards below map
 * 1:1 to the W9 read-only views.
 */

import Link from "next/link";

interface Card {
  href: "/lake" | "/templates" | "/decisions" | "/promotions";
  title: string;
  blurb: string;
}

const CARDS: ReadonlyArray<Card> = [
  {
    href: "/lake",
    title: "Lake roster",
    blurb:
      "Every candidate's current state, allocation, and breaker status. The state-machine view of the strategy lake.",
  },
  {
    href: "/templates",
    title: "Recent extractions",
    blurb:
      "Templates the extractor-worker produced this week, with the LLM-as-judge verdict and rationale.",
  },
  {
    href: "/decisions",
    title: "Decision audit log",
    blurb:
      "Each decision-engine cycle: action, reasoning, whether it passed the verification gate, prompt cost.",
  },
  {
    href: "/promotions",
    title: "Pending promotions",
    blurb:
      "Open promotion-request reply tokens waiting on operator approval in Discord.",
  },
];

const styles = {
  h1: { fontSize: "1.5rem", fontWeight: 600, margin: 0 } as const,
  lede: { color: "#9ca3af", marginTop: "0.5rem", maxWidth: "44rem" } as const,
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(18rem, 1fr))",
    gap: "1rem",
    marginTop: "2rem",
  } as const,
  card: {
    display: "block",
    padding: "1.25rem",
    background: "#111418",
    border: "1px solid #1f2937",
    borderRadius: "0.5rem",
    textDecoration: "none",
    color: "inherit",
  } as const,
  cardTitle: { fontSize: "1rem", fontWeight: 600, color: "#60a5fa" } as const,
  cardBlurb: {
    fontSize: "0.875rem",
    color: "#9ca3af",
    marginTop: "0.5rem",
    lineHeight: 1.5,
  } as const,
};

export default function Page() {
  return (
    <div>
      <h1 style={styles.h1}>Icarus — Insight</h1>
      <p style={styles.lede}>
        Read-only sidecar for the strategy-lake trading bot. Use Discord for alerts and approvals;
        come here to investigate <em>what the bot did this week and why</em>.
      </p>
      <div style={styles.grid}>
        {CARDS.map((card) => (
          <Link key={card.href} href={card.href} style={styles.card}>
            <div style={styles.cardTitle}>{card.title}</div>
            <div style={styles.cardBlurb}>{card.blurb}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
