# Icarus v2 — Daedalus

Autonomous DeFi trading bot organized as a strategy-lake architecture.
Single-operator personal project trading Base + Solana.

## Status

**Implementation branch (`daedalus`).** Build target: 12 weeks aggressive / 16-18 weeks realistic.
v4.2 launch-ready codebase preserved at `.archive/`.

## Design

See **`docs/blueprint.md`** for the full architecture and build sequence.

## Architecture in one paragraph

The bot ingests strategies from research sources (papers, blogs, on-chain analytics), extracts each as a parameterized strategy *template*, grid-searches the parameter space, walk-forward validates the top-K configurations, paper-trades them, and allocates capital across the validated lake using risk-parity. Built as 9 services in 3 domain clusters (Research / Curation / Execution) with Postgres as the cluster seam. LLMs run as advisors throughout the capital path; rules-based regime classification is the primary signal. Cycle never blocks on inference availability.

## Build

The daedalus branch starts effectively empty (only `.archive/`, root config files, and the blueprint).
Week 1 of the blueprint's build sequence creates the service skeletons (`decision-engine/`, `lake-governor/`, `webapp/`, `extractor-worker/`, `backtest-worker/`, `ts-executor/`, `solana-executor/`) with shared library code in `lib/`.

The repo is not runnable until week 1 day 1 of the build is complete.

## Rollback

If v2 misbehaves in production, `.archive/` contains the working v4.2 codebase.
See the blueprint's **Rollback to v4.2** section for the step-by-step procedure (~5 minutes for Base; Solana positions require manual operator action via Squads multisig).
