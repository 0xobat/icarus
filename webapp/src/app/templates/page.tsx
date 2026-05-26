/**
 * Recent template extractions — most recent first, filterable by judge_verdict.
 *
 * Source table: `templates` (DB model: Template in lib/src/icarus/db/models.py).
 * Operator workflow: extractor-worker proposes a template each cycle, the
 * LLM-as-judge stamps a verdict (PASS / FLAG_FOR_OPERATOR / REJECT). This page
 * surfaces what landed this week so the operator can spot-check the
 * FLAG_FOR_OPERATOR pile.
 */

import Link from "next/link";
import { query } from "@/lib/db";
import type { JudgeVerdict, TemplateRow } from "@/lib/types";
import {
  EmptyRow,
  ErrorBox,
  formatDate,
  tableStyles,
} from "../_components/Table";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 100;

const VALID_VERDICTS = new Set<JudgeVerdict>(["PASS", "FLAG_FOR_OPERATOR", "REJECT"]);

function parseVerdict(
  raw: string | string[] | undefined
): JudgeVerdict | "ALL" {
  if (typeof raw === "string" && VALID_VERDICTS.has(raw as JudgeVerdict)) {
    return raw as JudgeVerdict;
  }
  return "ALL";
}

interface PageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function TemplatesPage({ searchParams }: PageProps) {
  const sp = await searchParams;
  const verdict = parseVerdict(sp.verdict);

  let rows: TemplateRow[] = [];
  let error: string | null = null;

  try {
    const sql =
      verdict === "ALL"
        ? `SELECT template_id, title, chain, protocol, judge_verdict,
                  judge_rationale, created_at
             FROM templates
            ORDER BY created_at DESC
            LIMIT $1`
        : `SELECT template_id, title, chain, protocol, judge_verdict,
                  judge_rationale, created_at
             FROM templates
            WHERE judge_verdict = $1
            ORDER BY created_at DESC
            LIMIT $2`;
    const params: unknown[] = verdict === "ALL" ? [PAGE_SIZE] : [verdict, PAGE_SIZE];
    const res = await query<TemplateRow>(sql, params);
    rows = res.rows;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <h1 style={tableStyles.h1}>Recent extractions</h1>
      <p style={tableStyles.lede}>
        Strategy templates the extractor-worker produced, with the LLM-as-judge plausibility
        verdict. FLAG_FOR_OPERATOR rows are the spot-check pile.
      </p>

      <div style={tableStyles.toolbar}>
        <span style={{ color: "#6b7280", fontSize: "0.875rem" }}>Filter:</span>
        {(["ALL", "PASS", "FLAG_FOR_OPERATOR", "REJECT"] as const).map((v) => (
          <FilterPill key={v} verdict={v} active={verdict === v} />
        ))}
      </div>

      {error && <ErrorBox>Database read failed: {error}</ErrorBox>}

      <div style={tableStyles.wrap}>
        <table style={tableStyles.table}>
          <thead>
            <tr>
              <th style={tableStyles.th}>template_id</th>
              <th style={tableStyles.th}>title</th>
              <th style={tableStyles.th}>chain</th>
              <th style={tableStyles.th}>protocol</th>
              <th style={tableStyles.th}>judge_verdict</th>
              <th style={tableStyles.th}>judge_rationale</th>
              <th style={tableStyles.th}>created_at</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !error ? (
              <EmptyRow cols={7} message="No templates match this filter." />
            ) : (
              rows.map((r) => (
                <tr key={r.template_id}>
                  <td style={tableStyles.td}><code>{r.template_id}</code></td>
                  <td style={tableStyles.td}>{r.title}</td>
                  <td style={tableStyles.td}>{r.chain}</td>
                  <td style={tableStyles.td}>{r.protocol}</td>
                  <td style={tableStyles.td}>
                    <span style={verdictBadge(r.judge_verdict)}>{r.judge_verdict}</span>
                  </td>
                  <td style={{ ...tableStyles.td, maxWidth: "32rem", color: "#9ca3af" }}>
                    {r.judge_rationale ?? <span style={{ color: "#4b5563" }}>—</span>}
                  </td>
                  <td style={tableStyles.td}>{formatDate(r.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilterPill({
  verdict,
  active,
}: {
  verdict: "ALL" | JudgeVerdict;
  active: boolean;
}) {
  const query = verdict === "ALL" ? {} : { verdict };
  const style: React.CSSProperties = {
    ...tableStyles.pill,
    background: active ? "#1e3a5f" : "#1f2937",
    color: active ? "#93c5fd" : "#9ca3af",
    textDecoration: "none",
    fontWeight: 500,
  };
  return (
    <Link href={{ pathname: "/templates", query }} style={style}>
      {verdict}
    </Link>
  );
}

function verdictBadge(v: JudgeVerdict): React.CSSProperties {
  const palette: Record<JudgeVerdict, { bg: string; fg: string }> = {
    PASS: { bg: "#14532d", fg: "#86efac" },
    FLAG_FOR_OPERATOR: { bg: "#3a2f1e", fg: "#fbbf24" },
    REJECT: { bg: "#4c1d1d", fg: "#fca5a5" },
  };
  const { bg, fg } = palette[v];
  return { ...tableStyles.pill, background: bg, color: fg };
}
