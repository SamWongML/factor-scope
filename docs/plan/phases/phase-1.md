# Phase 1 — Ingestion + point-in-time store (L1 + L2)  ·  STATUS: done

Spec: §04 (data sources), §09 (storage). Decisions: D1 (fixtures-first), D6.

## Goal
Replace the hand-written fixture item list with data read from a **point-in-time store** that is
populated by **ingestion adapters**. After this phase, `factor-scope ingest` fills the store and
`factor-scope run` builds the dashboard from it, with real `evidence[]` and per-item gain.

## Design
- `factor_scope/ingest/` — one adapter per source, each with a **fixture backend (default)** and a
  **live backend (`--live`)** behind a common interface. Sources:
  - `positions.py` — load `positions.csv` → `{code, name, cost_basis, shares, list}`. (The user's
    book; the simplest point-in-time source. Start here.)
  - `prices.py` — CN prices/ETF NAV (AkShare `fund_etf_*` / Baostock). Live is opt-in.
  - `fund_holdings.py` — quarterly fund/ETF holdings (AkShare `fund_report_*_cninfo`) → graph edges later.
  - `fred.py` — macro series (DGS10, DFII10, T10YIE, DTWEXBGS, DEXCHUS, WALCL).
  - `edgar.py` — EdgarTools 13F + monthly N-PORT (US edges). Live is opt-in.
- `factor_scope/store/` — **DuckDB + Parquet**, append-only. Every row stamped `as_of` +
  `fetched_at`; **never overwrite**. Reads are point-in-time: "as of date D" returns the latest row
  with `as_of <= D` (a later disclosure must not change an earlier as-of read).
- `pipeline.build_dashboard` reads the store (still fixture-backed by default) to produce items +
  `evidence[]`; per-item gain = cost basis vs current NAV.
- New extra group already declared: `store` (duckdb, pyarrow). Add an `integration` test dir.

## TDD plan (RED → GREEN per unit)
1. **positions adapter** — `test_positions_adapter.py`: parses a fixture CSV; rejects malformed rows;
   maps to the contract list names. → `ingest/positions.py`.
2. **store contract** — `test_store_pit.py`: append-only (no in-place update); point-in-time read
   returns the correct as-of row; two disclosures of the same key keep both, read picks the right one.
   → `store/duckdb_store.py` (+ a thin interface so backends are swappable).
3. **prices/holdings/fred/edgar adapters** — fixture-backed parse tests; one live smoke per adapter
   marked `integration` + skipped unless `FACTOR_SCOPE_LIVE=1`.
4. **pipeline wiring** — `run` reads the store; items carry `evidence[]` + computed gain.

## System test (the gate)
`tests/system/test_ingest_run.py`: `ingest` (fixtures) → store populated → `run` yields the three
lists with non-empty `evidence[]` and per-item gain; artifact still schema-valid and deterministic.

## Done when
`make test` + `make system` green; `make run` shows items sourced from the store; append-only +
point-in-time invariants covered by tests; live paths behind `--live` only (never in CI). Update
`PROGRESS.md` + commit.
