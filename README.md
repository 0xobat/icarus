# Icarus v2 — Daedalus

Autonomous DeFi trading bot organized as a strategy-lake architecture.
Single-operator personal project trading Base + Solana.

## Status

**🟢 Build complete. Pre-live-capital.** 12-week blueprint shipped.
65 commits on the `daedalus` branch ahead of `origin/main`.

| | |
|---|---|
| `harness/verify.sh` | 19 pass / 1 skip / 0 fail |
| Tests | **1,019 passing** (764 pytest + 222 ts-executor vitest + 33 solana-executor vitest) |
| Reference templates | 5 (3 Base, 2 Solana) |
| Services | 9 (Research / Curation / Execution + Postgres / Redis / Ollama infra) |
| Go/no-go verdict | See `docs/W12-go-no-go.md` — **structurally complete** |

v4.2 codebase preserved at `.archive/` for rollback.

## Design

See **`docs/blueprint.md`** for the full architecture and build sequence.
See **`docs/W12-go-no-go.md`** for the post-build per-criterion go/no-go.

## Architecture in one paragraph

The bot ingests strategies from research sources (papers, blogs, on-chain
analytics), extracts each as a parameterized strategy *template* via a frontier
LLM with an LLM-as-judge plausibility check, grid- or Bayesian-searches the
parameter space, walk-forward validates the top-K configurations with deflated
Sharpe + BH multi-test correction, paper-trades them for ≥14 days, and
allocates capital across the validated lake using risk-parity. Built as 9
services in 3 domain clusters (Research / Curation / Execution) with Postgres
LISTEN/NOTIFY as the cluster seam. LLMs run as advisors throughout the capital
path; rules-based regime classification is the primary signal. Cycle never
blocks on inference availability.

## Service layout

```
Research cluster (offline, bursty)
  extractor-worker     paper/blog → 4-file template via Anthropic + agentic repair
  backtest-worker      grid + Bayesian (Optuna) search; walk-forward via vectorbt

Curation cluster (continuous, low-throughput)
  lake-governor        paper-trade harness + state machine + decay/breaker + promotion gate
  webapp               Next.js read-only insight UI (lake/templates/decisions/promotions)
  grafana              4 dashboards on Postgres (lake/templates/allocations/breakers)

Execution cluster (continuous, latency-sensitive)
  decision-engine      cycle + regime classifier + allocator + risk gate
  ts-executor          Base — Safe multisig (verbatim from v4.2)
  solana-executor      Solana — Squads multisig (operator-scaffold for v1)
  inference            Ollama (DeepSeek-R1-Distill-Qwen-14B), advisory only

Shared
  postgres             state of record + LISTEN/NOTIFY cross-cluster bus
  redis                ephemeral bus + queues (BLMOVE atomic claim)
```

## Run

```bash
# 1. Configure
cp .env.example .env   # fill ANTHROPIC, ALCHEMY+WALLET+SAFE, HELIUS+SOLANA_KEYPAIR, DISCORD, DUNE

# 2. Bring up the stack
docker compose up -d --wait

# 3. Verify
bash harness/verify.sh                    # 19 pass / 1 skip / 0 fail (skip = cluster-isolation needs docker)
bash harness/e2e_smoke.sh                 # paper → paper-trade → orders on both chains
bash harness/breaker_dryrun.sh            # 6 circuit breakers exercised
bash harness/signing_dryrun.sh            # Discord round-trip + Safe/Squads signing (keys required for Phase 2/3)

# 4. Ingest a paper (operator workflow)
python -m extractor_worker.enqueue paper_pdf path/to/paper.pdf   # or blog_url <url>

# 5. Watch
open http://localhost:3000    # webapp insight UI
open http://localhost:3001    # Grafana dashboards
# Discord channel receives PROMOTION REQUEST after 14d paper-trade observation.
# Reply: `APPROVE tok-<id>` (token id is printed in the request). Only authors
# whose user-id is in DISCORD_OPERATOR_USER_IDS can authorize a promotion.
```

## Reference templates

| ID | Chain | Strategy shape |
|---|---|---|
| `LEND-001` | Base / Aave V3 | Stablecoin supply rotation |
| `BASIS-PERP-001` | Base / Synthetix Perps | ETH delta-neutral basis trade |
| `LEND-KAMINO-001` | Solana / Kamino | Stablecoin supply rotation (cross-chain analog of LEND-001) |
| `BASIS-SOL-DRIFT-001` | Solana / Drift | SOL/ETH delta-neutral basis trade |
| `LP-AERO-001` | Base / Aerodrome | Stablecoin LP yield rotation |

Templates live in `templates/<id>/` as 4-file directories (manifest.yaml +
evaluate.py + smoke_test.py + parameter_rationale.md). New templates can be
hand-authored or extractor-emitted from a paper/blog; either lands the same way.

## Pre-live-capital checklist

Per `docs/W12-go-no-go.md`. The build is structurally complete; the remaining
gates are operator-side, not code-side:

- [ ] `git push origin daedalus`
- [ ] Fill `.env` with real keys, including post-security-review additions:
  - `DECISION_ENGINE_TOTAL_CAPITAL_USD` — required; decision-engine fails to boot without it (exposure limiter divides by this)
  - `DISCORD_OPERATOR_USER_IDS` — required for any Discord reply to authorize anything (empty allowlist is fail-closed)
  - `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` — required for the reply-ingestion long-poll path
- [ ] `docker compose up -d --wait` reports all healthy
- [ ] `bash harness/verify.sh` → 19/1/0 (or 20/0/0 with docker up)
- [ ] `bash harness/signing_dryrun.sh` Phase 2+3 PASS with real keys
- [ ] Enqueue first real paper; verify top-K → paper-trade in <5d. Note: templates with `judge_verdict=REJECT` are refused at load time; inspect any rejection in the webapp before re-extracting.
- [ ] Wait 14d for paper-trade observation; reply `APPROVE tok-<id>` to the first PROMOTION REQUEST in Discord from an allowlisted operator account

## Conventions

- All logs structured JSON with `timestamp`, `service`, `event`, `correlationId`.
- Postgres is state of record. Redis is ephemeral bus + queues.
- LLM calls are advisory only, never inside capital-protecting gates.
- Cross-cluster reads via Postgres (with LISTEN/NOTIFY for eventness), never service-to-service direct calls.
- Verification gate non-negotiable: orders pass risk pre-trade → exposure → circuit breakers → schema validation before execution.
- One strategy adjustment per decision cycle.

See `CLAUDE.md` for the full convention set.

## Rollback

If v2 misbehaves in production, `.archive/` contains the working v4.2 codebase.
See the blueprint's **Rollback** section for the step-by-step procedure
(~5 minutes for Base; Solana positions require manual operator action via
Squads multisig).

## What's deferred (slack weeks 13-18)

Per the W12 go/no-go doc, these aren't blockers for first live promotion but
are the natural priorities for the second:

- **Drift adapter** (Node) — BASIS-SOL-DRIFT-001 template references it; currently stubbed
- **Squads multisig real signing** — single-signer fallback shipped for v1
- **Per-position chain awareness** in W3 breakers (currently hardcode `chain="base"`)
- **Decay + per-template breaker runtime loop** in lake-governor (detectors exist; loop is TODO'd)
- **Reply-token TTL sweep** as a separate background cadence
