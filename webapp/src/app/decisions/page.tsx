/**
 * Decision audit log — every decision-engine cycle, newest first.
 *
 * Source table: `decision_audit_log` (DB model: DecisionAuditLog). The
 * blueprint's verification gate is the load-bearing capital protector; the
 * `passed_verification` column is therefore the most important column on this
 * page — any FALSE row is an order the bot wanted to send but couldn't.
 */

import { query } from "@/lib/db";
import type { DecisionAuditRow } from "@/lib/types";
import {
  EmptyRow,
  ErrorBox,
  formatDate,
  tableStyles,
} from "../_components/Table";

export const dynamic = "force-dynamic";

const LIMIT = 200;

export default async function DecisionsPage() {
  let rows: DecisionAuditRow[] = [];
  let error: string | null = null;

  try {
    const res = await query<DecisionAuditRow>(
      `SELECT id, timestamp, decision_action, reasoning,
              passed_verification, prompt_tokens
         FROM decision_audit_log
        ORDER BY timestamp DESC
        LIMIT $1`,
      [LIMIT]
    );
    rows = res.rows;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <h1 style={tableStyles.h1}>Decision audit log</h1>
      <p style={tableStyles.lede}>
        Each decision-engine cycle: what it picked, why, and whether the verification gate let it
        through. Capped at the most recent {LIMIT} cycles.
      </p>

      {error && <ErrorBox>Database read failed: {error}</ErrorBox>}

      <div style={tableStyles.wrap}>
        <table style={tableStyles.table}>
          <thead>
            <tr>
              <th style={tableStyles.th}>timestamp</th>
              <th style={tableStyles.th}>decision_action</th>
              <th style={tableStyles.th}>reasoning</th>
              <th style={tableStyles.th}>passed_verification</th>
              <th style={tableStyles.th}>prompt_tokens</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !error ? (
              <EmptyRow cols={5} message="No decisions logged yet." />
            ) : (
              rows.map((r) => (
                <tr key={r.id}>
                  <td style={tableStyles.td}>{formatDate(r.timestamp)}</td>
                  <td style={tableStyles.td}><code>{r.decision_action}</code></td>
                  <td style={{ ...tableStyles.td, maxWidth: "40rem", color: "#9ca3af" }}>
                    {r.reasoning ?? <span style={{ color: "#4b5563" }}>—</span>}
                  </td>
                  <td style={tableStyles.td}>
                    {r.passed_verification ? (
                      <span
                        style={{
                          ...tableStyles.pill,
                          background: "#14532d",
                          color: "#86efac",
                        }}
                      >
                        PASS
                      </span>
                    ) : (
                      <span
                        style={{
                          ...tableStyles.pill,
                          background: "#7f1d1d",
                          color: "#fecaca",
                        }}
                      >
                        FAIL
                      </span>
                    )}
                  </td>
                  <td style={tableStyles.td}>
                    {r.prompt_tokens ?? <span style={{ color: "#4b5563" }}>—</span>}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
