/**
 * Lake roster — state-machine view of every candidate the bot owns.
 *
 * Read straight from the `lake_roster` table (see DB model: LakeRoster in
 * `lib/src/icarus/db/models.py`). Sort + paginate via URL search params so
 * everything stays in a server component — no client JS needed for the
 * common Saturday-morning "show me what's live" pull.
 */

import Link from "next/link";
import { query } from "@/lib/db";
import type { LakeRosterRow } from "@/lib/types";
import {
  EmptyRow,
  ErrorBox,
  formatDate,
  formatPct,
  formatUsd,
  tableStyles,
} from "../_components/Table";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;

// Whitelist sortable columns — we splice the column name straight into SQL so
// it MUST come from a fixed set. Direction is similarly whitelisted.
const SORT_COLUMNS = {
  candidate_id: "candidate_id",
  template_id: "template_id",
  state: "state",
  allocation_usd: "allocation_usd",
  allocation_max_pct: "allocation_max_pct",
  last_transition_at: "last_transition_at",
  breaker_tripped: "breaker_tripped",
} as const;

type SortKey = keyof typeof SORT_COLUMNS;

function parseSort(raw: string | string[] | undefined): SortKey {
  if (typeof raw === "string" && raw in SORT_COLUMNS) return raw as SortKey;
  return "last_transition_at";
}

function parseDir(raw: string | string[] | undefined): "asc" | "desc" {
  return raw === "asc" ? "asc" : "desc";
}

function parsePage(raw: string | string[] | undefined): number {
  const n = typeof raw === "string" ? parseInt(raw, 10) : 1;
  return Number.isFinite(n) && n > 0 ? n : 1;
}

interface PageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function LakePage({ searchParams }: PageProps) {
  const sp = await searchParams;
  const sort = parseSort(sp.sort);
  const dir = parseDir(sp.dir);
  const page = parsePage(sp.page);
  const offset = (page - 1) * PAGE_SIZE;

  let rows: LakeRosterRow[] = [];
  let total = 0;
  let error: string | null = null;

  try {
    const [data, count] = await Promise.all([
      query<LakeRosterRow>(
        `SELECT candidate_id, template_id, state,
                allocation_usd, allocation_max_pct,
                last_transition_at, breaker_tripped
           FROM lake_roster
          ORDER BY ${SORT_COLUMNS[sort]} ${dir.toUpperCase()}
          LIMIT $1 OFFSET $2`,
        [PAGE_SIZE, offset]
      ),
      query<{ count: string }>(`SELECT COUNT(*)::text AS count FROM lake_roster`),
    ]);
    rows = data.rows;
    total = parseInt(count.rows[0]?.count ?? "0", 10);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      <h1 style={tableStyles.h1}>Lake roster</h1>
      <p style={tableStyles.lede}>
        Every candidate the lake-governor is tracking, with its current state-machine position,
        capital allocation, and breaker status.
      </p>

      {error && <ErrorBox>Database read failed: {error}</ErrorBox>}

      <div style={tableStyles.wrap}>
        <table style={tableStyles.table}>
          <thead>
            <tr>
              {(Object.keys(SORT_COLUMNS) as SortKey[]).map((col) => (
                <SortHeader key={col} col={col} sort={sort} dir={dir} />
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !error ? (
              <EmptyRow cols={7} message="No rows in lake_roster yet." />
            ) : (
              rows.map((r) => (
                <tr key={r.candidate_id}>
                  <td style={tableStyles.td}><code>{r.candidate_id}</code></td>
                  <td style={tableStyles.td}><code>{r.template_id}</code></td>
                  <td style={tableStyles.td}>
                    <span style={stateBadge(r.state)}>{r.state}</span>
                  </td>
                  <td style={tableStyles.td}>{formatUsd(r.allocation_usd)}</td>
                  <td style={tableStyles.td}>{formatPct(r.allocation_max_pct)}</td>
                  <td style={tableStyles.td}>{formatDate(r.last_transition_at)}</td>
                  <td style={tableStyles.td}>
                    {r.breaker_tripped ? (
                      <span style={{ ...tableStyles.pill, background: "#7f1d1d", color: "#fecaca" }}>
                        TRIPPED
                      </span>
                    ) : (
                      <span style={{ color: "#6b7280" }}>—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} totalPages={totalPages} sort={sort} dir={dir} total={total} />
    </div>
  );
}

function SortHeader({
  col,
  sort,
  dir,
}: {
  col: SortKey;
  sort: SortKey;
  dir: "asc" | "desc";
}) {
  const isActive = sort === col;
  const nextDir = isActive && dir === "desc" ? "asc" : "desc";
  const arrow = isActive ? (dir === "desc" ? " ↓" : " ↑") : "";
  return (
    <th style={tableStyles.th}>
      <Link
        href={{ pathname: "/lake", query: { sort: col, dir: nextDir } }}
        style={{ color: isActive ? "#60a5fa" : "#9ca3af", textDecoration: "none" }}
      >
        {col}
        {arrow}
      </Link>
    </th>
  );
}

function Pagination({
  page,
  totalPages,
  sort,
  dir,
  total,
}: {
  page: number;
  totalPages: number;
  sort: SortKey;
  dir: "asc" | "desc";
  total: number;
}) {
  return (
    <div style={{ ...tableStyles.toolbar, justifyContent: "space-between" }}>
      <div style={{ color: "#6b7280", fontSize: "0.875rem" }}>
        Page {page} of {totalPages} · {total} candidate{total === 1 ? "" : "s"}
      </div>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <PageLink page={page - 1} sort={sort} dir={dir} disabled={page <= 1}>
          ← Prev
        </PageLink>
        <PageLink page={page + 1} sort={sort} dir={dir} disabled={page >= totalPages}>
          Next →
        </PageLink>
      </div>
    </div>
  );
}

function PageLink({
  page,
  sort,
  dir,
  disabled,
  children,
}: {
  page: number;
  sort: SortKey;
  dir: "asc" | "desc";
  disabled: boolean;
  children: React.ReactNode;
}) {
  const style = {
    padding: "0.25rem 0.75rem",
    borderRadius: "0.25rem",
    border: "1px solid #1f2937",
    color: disabled ? "#4b5563" : "#e5e7eb",
    textDecoration: "none",
    pointerEvents: disabled ? ("none" as const) : ("auto" as const),
    fontSize: "0.875rem",
  };
  if (disabled) return <span style={style}>{children}</span>;
  return (
    <Link href={{ pathname: "/lake", query: { sort, dir, page } }} style={style}>
      {children}
    </Link>
  );
}

function stateBadge(state: string): React.CSSProperties {
  const palette: Record<string, { bg: string; fg: string }> = {
    backtest: { bg: "#1e3a5f", fg: "#93c5fd" },
    paper_trade: { bg: "#3a2f1e", fg: "#fbbf24" },
    live_capped: { bg: "#1e4a3a", fg: "#6ee7b7" },
    live_mature: { bg: "#14532d", fg: "#86efac" },
    demoted_paper: { bg: "#4c1d1d", fg: "#fca5a5" },
    archived: { bg: "#1f2937", fg: "#9ca3af" },
  };
  const { bg, fg } = palette[state] ?? { bg: "#1f2937", fg: "#9ca3af" };
  return { ...tableStyles.pill, background: bg, color: fg };
}
