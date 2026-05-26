/**
 * Pending promotion requests — the Discord reply-token outbox.
 *
 * Source table: `discord_reply_tokens` (DB model: DiscordReplyToken). The
 * webapp surfaces what's *pending* so the operator can spot a forgotten
 * approval before it expires. Approval itself happens in Discord — this is a
 * pull-only mirror. Rows past `expires_at` get swept to "expired" by the
 * lake-governor tick loop, so we don't show them here.
 */

import { query } from "@/lib/db";
import type { PromotionRequestRow } from "@/lib/types";
import {
  EmptyRow,
  ErrorBox,
  formatDate,
  tableStyles,
} from "../_components/Table";

export const dynamic = "force-dynamic";

function timeUntil(future: Date): string {
  const ms = new Date(future).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  if (hours >= 1) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export default async function PromotionsPage() {
  let rows: PromotionRequestRow[] = [];
  let error: string | null = null;

  try {
    const res = await query<PromotionRequestRow>(
      `SELECT id, candidate_id, template_id, kind, status,
              created_at, expires_at
         FROM discord_reply_tokens
        WHERE status = 'pending'
        ORDER BY created_at DESC`
    );
    rows = res.rows;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <h1 style={tableStyles.h1}>Pending promotions</h1>
      <p style={tableStyles.lede}>
        Open promotion-request reply tokens — the lake-governor posted these to Discord and is
        waiting on operator action. Approve / reject via Discord; this page is read-only.
      </p>

      {error && <ErrorBox>Database read failed: {error}</ErrorBox>}

      <div style={tableStyles.wrap}>
        <table style={tableStyles.table}>
          <thead>
            <tr>
              <th style={tableStyles.th}>candidate_id</th>
              <th style={tableStyles.th}>template_id</th>
              <th style={tableStyles.th}>kind</th>
              <th style={tableStyles.th}>created_at</th>
              <th style={tableStyles.th}>expires_at</th>
              <th style={tableStyles.th}>time left</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !error ? (
              <EmptyRow cols={6} message="No pending promotion requests." />
            ) : (
              rows.map((r) => {
                const remaining = timeUntil(r.expires_at);
                const urgent = remaining === "expired" || remaining.endsWith("m");
                return (
                  <tr key={r.id}>
                    <td style={tableStyles.td}><code>{r.candidate_id}</code></td>
                    <td style={tableStyles.td}><code>{r.template_id}</code></td>
                    <td style={tableStyles.td}>{r.kind}</td>
                    <td style={tableStyles.td}>{formatDate(r.created_at)}</td>
                    <td style={tableStyles.td}>{formatDate(r.expires_at)}</td>
                    <td style={tableStyles.td}>
                      <span
                        style={{
                          ...tableStyles.pill,
                          background: urgent ? "#7f1d1d" : "#3a2f1e",
                          color: urgent ? "#fecaca" : "#fbbf24",
                        }}
                      >
                        {remaining}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
