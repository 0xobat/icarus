/**
 * Webapp landing — placeholder index listing the planned views.
 *
 * Per blueprint §"Curation cluster components — Webapp insight sidecar"
 * (line 329 onward), the webapp surfaces *bot reasoning*: what the
 * allocator decided this cycle and why, the LLM advisor commentary,
 * candidate-level decision traces, the lake state machine, recent
 * template extraction results, current paper-trade observation windows.
 *
 * Week 9 of the build sequence wires the real views. Week 1 day 1
 * ships only this skeleton so `pnpm dev` boots and the route table is
 * already declared.
 */

export default function Page() {
  const views = [
    { path: "/allocator", title: "Allocator commentary", week: "8" },
    { path: "/lake", title: "Lake state machine", week: "5" },
    { path: "/candidates", title: "Candidate-level decision traces", week: "9" },
    { path: "/templates", title: "Recent template extractions (awaiting plausibility review)", week: "2" },
    { path: "/regime", title: "Regime classifier output (rules vs LLM advisor disagreement)", week: "6" },
  ];

  return (
    <main style={{ padding: "2rem", maxWidth: "60rem", margin: "0 auto" }}>
      <h1 style={{ fontSize: "1.5rem", fontWeight: 600 }}>Icarus — Insight</h1>
      <p style={{ color: "#9ca3af", marginTop: "0.5rem" }}>
        Read-only insight sidecar. Discord remains the push channel for alerts and approvals;
        this app is for pull-style investigation: <em>what is the bot thinking right now and why</em>.
      </p>
      <h2 style={{ fontSize: "1rem", fontWeight: 600, marginTop: "2rem", color: "#9ca3af" }}>
        Planned views (week 9 build)
      </h2>
      <ul style={{ listStyle: "none", padding: 0, marginTop: "0.75rem" }}>
        {views.map((v) => (
          <li
            key={v.path}
            style={{
              padding: "0.5rem 0",
              borderBottom: "1px solid #1f2937",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span>
              <code style={{ color: "#60a5fa" }}>{v.path}</code> — {v.title}
            </span>
            <span style={{ color: "#6b7280", fontSize: "0.875rem" }}>w{v.week}</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
