# PROGRESS — live handoff board

> Single source of truth for "where are we". A fresh session reads `CLAUDE.md` + this file + the
> current `phases/phase-N.md` and resumes. Update status + **NEXT ACTION** whenever a unit or phase
> closes, then commit. Statuses: `todo` · `wip` · `done`.

**Current phase:** Phase 3 — Connection graph + look-through
**NEXT ACTION:** Start Phase 3 RED — pick the graph engine (open item in `DECISIONS.md`: embedded
on-disk default behind a `GraphStore` interface vs Neo4j Community) and record it, then write
`tests/unit/` for the deterministic, exact set-arithmetic look-through over the `fund_holdings` /
`edgar` readings already in the store: a falling name → who else of mine holds it + my total
look-through weight. Attach `connections[]` + `connections_flag` to each item. See `phases/phase-3.md`.

| Phase | Status | Closed by (commit) | System test |
|-------|--------|--------------------|-------------|
| 0 — Scaffold, contract, entrypoint, tracking | **done** | _initial Phase 0 commit_ | `make system` ✓ (7 tests) |
| 1 — Ingestion + point-in-time store | **done** | _Phase 1 commit_ | `make system` ✓ (5 tests) |
| 2 — Factor states + trend gate | **done** | _this commit_ | `make system` ✓ (8 tests) |
| 3 — Connection graph + look-through | todo | — | — |
| 4 — Self-scoring loop | todo | — | — |
| 5 — Digestion: LLM provider + bull/bear | todo | — | — |
| 6 — Emerging radar funnel | todo | — | — |
| 7 — Scheduling, packaging & ops | todo | — | — |

## Phase 2 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Factors** (`factor_scope/factors`): `bands` (mid-rank `percentile_rank` + `rank_to_band` on
  constant 5/25/75/95 cut-points — economic meaning, never tuned to P&L), `window` (point-in-time
  series helpers: `price_navs`/`fred_values`, returns, rolling vol, drawdown, 200-day MA), and
  `battery` (the 8 states as pure `(FactorContext)->FactorState` functions in spec order + the hard
  `compute_gate`). **No composite anywhere.**
- Data-backed today (read from the store): **trend gate** (price vs 200-day MA → the gate),
  **reversal** (20d return vs own history; up-stretch → reversal-DOWN risk), **low-vol/drawdown**
  (rolling-vol percentile + drawdown), **macro dial** (DFII10 real-yield percentile, book-wide).
  Present-but-invalid until sources land: cross-market lead, crowding, demand, valuation
  (`valid=false`, never dropped, never raises).
- Pipeline attaches `states[]` + `gate` per item; render surfaces the active (non-neutral) reads.
- Fixtures gained real history (`scripts/gen_fixtures_phase2.py`, deterministic + seedless):
  `prices.csv` now ~220 weekdays/code (3 above their 200-MA = gate `open`, the energy-storage ETF in
  a ~33% drawdown below it = `capped`), `fred.csv` gained a 2-yr monthly DFII10 series.
- Green: `make check` (46 passed, 2 live skipped), `make run` shows the gate + states per item.

## Phase 1 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Store** (`factor_scope/store`): `Reading` + `PointInTimeStore` protocol + append-only `DuckDBStore`
  (file or `:memory:`). `read_as_of` is point-in-time per key; `history` is the audit trail.
- **Ingest** (`factor_scope/ingest`): adapters for positions, prices, fund_holdings, fred, edgar —
  each a fixture backend (default) + an opt-in lazy `fetch_live` (behind `--live`, never in CI).
- Pipeline now `ingest`s into the store and `build_dashboard` reads it as-of the run date → items
  carry real `evidence[]` + a per-item `gain` (cost basis vs NAV). New `gain` field on the contract.
- New CLI `factor-scope ingest` (+ `--store-path` on `run`). Fixtures: `positions.csv`, `prices.csv`,
  `fund_holdings.csv`, `fred.csv`, `edgar.csv`, `manifest.json` (replacing `items.json`).
- CI + `make setup` now install the `store` extra. Green: `make test` (25 passed, 2 live skipped),
  `make lint`, `make typecheck`; `make run` shows store-sourced, point-in-time items.

## Phase 0 — done
RED→GREEN→REFACTOR complete. Delivered:
- Project tooling: `pyproject.toml` (uv, hatchling), `Makefile`, pytest markers, ruff, mypy strict, CI.
- **Contract** (`factor_scope/contract`): all `dashboard.json` models + JSON-schema export.
- Pipeline spine + `render` + `cli` (`run`, `schema`). Fixture `data/fixtures/items.json`.
- Process-tracking scaffold: `CLAUDE.md`, `docs/spec/`, `docs/plan/` (ROADMAP, PROGRESS, DECISIONS,
  phases/), `docs/ARCHITECTURE.md`.
- Green: `make test` (7 passed), `make lint`, `make typecheck`; `make run` prints the artifact.

## Notes for the next session
- Keep `factor-scope run --fixtures` green at every commit (the invariant).
- The point-in-time store is the seam: factors read `prices`/`fred` via `store.history(...)` filtered
  to `as_of <=` the run date (see `factor_scope/factors/window.py`). Adding a new data-backed factor
  = enrich the fixtures + flip its `_unavailable(...)` stub to a real band; no new ingestion needed
  unless a brand-new source is required.
- Phase 3 reads the `fund_holdings` / `edgar` readings already in the store (no new ingestion) to
  build the deterministic look-through. Record the graph-engine choice in `DECISIONS.md` (open item).
- Live fetchers stay behind `--live`, lazily imported, never in CI (smokes skip unless
  `FACTOR_SCOPE_LIVE=1`). The `store` extra (duckdb) is installed by CI + `make setup`.
- Fixtures are regenerated, not hand-edited: `uv run python scripts/gen_fixtures_phase2.py`
  (deterministic + seedless, so the artifact stays byte-for-byte reproducible).
