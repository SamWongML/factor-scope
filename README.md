# factor-scope

**The Wealth-Assistant Engine** — a local-first, single-user, **nightly-batch** decision-support
engine for an A-share / funds-and-ETFs portfolio. Each run emits one dated artifact,
**`dashboard.json`** (three lists — *holdings · watchlist · emerging* — plus `connections[]` and a
`scorecard`), which you review each morning. It shortens your **lag**, not your **risk**. **The
engine never places orders — you remain the only thing that clicks buy.**

> Not financial advice. Educational research tooling. See the disclaimer in the source spec.

## Quickstart

```bash
make setup      # uv venv + install (uses uv as the package manager)
make run        # build the morning artifact from bundled fixtures and print it
make test       # full offline test suite (unit + integration + system)
make check      # lint + typecheck + test (what CI runs)
```

`make run` writes `out/dashboard.json` and prints a terminal summary. Everything runs **offline on
bundled sample data** by default; live data sources and real LLM providers are opt-in.

## What it does (the six layers)
1. **Ingest** free data — CN (AkShare/Baostock/Mootdx), US lead (EdgarTools/FRED), your `positions.csv`.
2. **Store** it **point-in-time** (DuckDB + Parquet, append-only) + a durable on-disk connection graph.
3. **Compute** descriptive **factor states** (never a fitted composite), the exact holdings
   **look-through**, the emerging radar, and the **self-scoring** scorecard.
4. **Digest** via **Claude Code (headless)** — a bull/bear debate then a synthesis that emits a
   calibrated lean; cheap chores go to **DeepSeek V4**.
5. **Emit** one `dashboard.json` (the contract everything reads/writes).
6. **Review** it each morning. Most mornings the right action is none. *Patience is a position.*

## Project status
Built phase by phase; the `factor-scope run` entrypoint stays runnable at every boundary.
- **Phase 0 — done:** scaffold, the `dashboard.json` contract, the entrypoint, and the cross-session
  tracking docs.
- Next: see **`docs/plan/PROGRESS.md`** (live state) and **`docs/plan/ROADMAP.md`** (the phases).

## For contributors / agents
Read **`CLAUDE.md`**, then `docs/plan/PROGRESS.md`, then the current `docs/plan/phases/phase-N.md`.
Work test-first (RED → GREEN → REFACTOR); keep `make test` + `make system` green; update
`PROGRESS.md` and commit when a unit or phase closes. Architecture + the contract:
`docs/ARCHITECTURE.md`. Full spec: `docs/spec/`.

```
factor-scope schema      # print the dashboard.json JSON schema
factor-scope run --help  # the entrypoint's options
```
