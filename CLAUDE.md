# Icarus v2 — Daedalus Branch

Implementation branch for Icarus v2 (Strategy Lake Architecture).
v4.2 codebase preserved at `.archive/` for reference and rollback.
See `docs/blueprint.md` for the approved design.

## Architecture summary

9-service, 3-cluster decomposition (full detail in `docs/blueprint.md`):

- **Research cluster** (offline, bursty): `extractor-worker`, `backtest-worker`
- **Curation cluster** (continuous, low-throughput): `lake-governor`, `webapp`, `grafana`
- **Execution cluster** (continuous, latency-sensitive): `decision-engine`, `ts-executor` (Base), `solana-executor` (Solana), `inference` (Ollama, advisory only)
- **Shared infrastructure**: `postgres` (state of record), `redis` (bus + queues)

## v4.2 reuse protocol

Per archive-first discipline: any module copied back from `.archive/` must
(a) run its existing tests against the unmodified `.archive/` copy and pass,
(b) be copied to the new tree, (c) re-run tests in the new location.
Apply to: `risk/`, `db/`, `ts-executor/`, `shared/schemas/`, `AllowlistGuard.sol`.

## Conventions

- All logs structured JSON with `timestamp`, `service`, `event`, `correlationId`.
- Postgres is state of record. Redis is ephemeral bus + queues.
- LLM calls are advisory only, never inside capital-protecting gates.
- Cross-cluster reads via Postgres (with LISTEN/NOTIFY for eventness), never service-to-service direct calls.
- Verification gate non-negotiable: orders pass through risk pre-trade gate, exposure limits, circuit breakers, and schema validation before execution.
- One strategy adjustment per decision cycle.

## Documentation

- `docs/blueprint.md` — approved v2 design (2026-05-04)
- `.archive/docs/system-design.md` — v4.2 spec (historical reference)
- `.archive/docs/design-note-v2.md` — v2 draft proposal (superseded by blueprint)

## Commit messages

```
feat(daedalus): description
fix(daedalus): description
chore(daedalus): description
```

## Rollback

If v2 misbehaves in production, `.archive/` contains the working v4.2 codebase.
See the blueprint's Rollback section for the step-by-step procedure.
The rollback anchor commit is `chore(daedalus): archive v4.2 codebase to .archive/ for archive-first build`.
