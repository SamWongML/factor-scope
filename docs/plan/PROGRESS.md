# PROGRESS — live handoff board

> Single source of truth for "where are we". A fresh session reads `CLAUDE.md` + this file + the
> current `phases/phase-N.md` and resumes. Update status + **NEXT ACTION** whenever a unit or phase
> closes, then commit. Statuses: `todo` · `wip` · `done`.

**Current phase:** Phase 4 — Self-scoring loop
**NEXT ACTION:** Start Phase 4 RED — write `tests/unit/` for the mechanical self-scoring (spec §06):
each resolved lean is a falsifiable claim (lean + confidence + horizon + invalidation), scored next
day by forward return vs stated direction → hit/miss/abstain (no LLM, no memory). Then the rolling
`scorecard`: Brier, Brier **skill** vs base rate, reliability-by-confidence, per-state-pattern
hit-rate — descriptive only, gated on a minimum sample. A resolved call is immutable (point-in-time).
Attach `scorecard` to the artifact. See `phases/phase-4.md`. Note: leans land in Phase 5, so Phase 4
scores against a fixture of prior calls (append-only) — wire the loop now, feed it real leans at P5.

| Phase | Status | Closed by (commit) | System test |
|-------|--------|--------------------|-------------|
| 0 — Scaffold, contract, entrypoint, tracking | **done** | _initial Phase 0 commit_ | `make system` ✓ (7 tests) |
| 1 — Ingestion + point-in-time store | **done** | _Phase 1 commit_ | `make system` ✓ (5 tests) |
| 2 — Factor states + trend gate | **done** | _Phase 2 commit_ | `make system` ✓ (8 tests) |
| 3 — Connection graph + look-through | **done** | _this commit_ | `make system` ✓ (10 tests) |
| 4 — Self-scoring loop | todo | — | — |
| 5 — Digestion: LLM provider + bull/bear | todo | — | — |
| 6 — Emerging radar funnel | todo | — | — |
| 7 — Scheduling, packaging & ops | todo | — | — |

## Phase 3 — done
RED→GREEN→REFACTOR complete. Delivered:
- **Graph** (`factor_scope/graph`): a `GraphStore` `Protocol` + default append-only `DuckDBGraphStore`
  (`(:Fund)-[:HOLDS{weight,as_of}]->(:Security)`, file or `:memory:`), point-in-time at query time
  (same `QUALIFY` latest-as-of as the readings store). `build_graph_from_store` materialises HOLDS
  edges straight from the `fund_holdings` readings (no LLM). Engine choice = **D8** (Kùzu/Neo4j the
  documented swap). **No NetworkX / in-memory rebuilt-each-run graph.**
- **Look-through** (`graph/lookthrough.py`): exact set arithmetic — `look_through` returns the funds
  of mine holding a security (point-in-time) + my total weight `Σ(weight in fund × my portfolio
  weight in fund)`; `build_connections` surfaces a name only when ≥2 of my funds share it (the
  illusion-of-diversification catch), with a `↓` when any holder fund is at downside risk.
- Pipeline builds the graph (durable when `--graph-path` given, else in-memory from the store) and
  attaches `connections[]` + `connections_flag` per item; portfolio weights from `shares × NAV`. A
  falling name inherits the `↓` across the book. Render shows the overlaps. New `--graph-path` flag
  on `run`/`ingest`; `Config.graph_path`.
- Fixture story (unchanged fixtures): **中际旭创** is held by both the optical-module (561010) and
  comms (515880) ETFs → surfaces on both with look-through ≈ **8.4%**, flagged `↓` (561010 reads
  reversal-DOWN risk). 新易盛 / 中芯国际 are single-fund → not surfaced.
- Green: `make check` (57 passed, 2 live skipped), `make run` shows the look-through connections.

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
- The connection graph is durable on disk (`DuckDBGraphStore`, `--graph-path`) or built in-memory
  from the readings store at run time when no path is given (mirrors the readings-store pattern).
  Look-through is exact set arithmetic, point-in-time at query time; `build_connections` is the seam
  Phase 6 reuses for emerging overlap-with-core. EDGAR (US lead chain) is deliberately *not* in the
  book graph (no weights, different universe) — see D8.
- Phase 4 (self-scoring, §06) is mechanical and LLM-free: it scores resolved leans by forward return
  vs stated direction. Leans land in Phase 5, so wire the scorer + `scorecard` now against a small
  append-only fixture of prior calls; feed it real leans at P5. A resolved call must be immutable
  (point-in-time) and the scorecard is **descriptive only** (never changes a state or opens the gate).
- Live fetchers stay behind `--live`, lazily imported, never in CI (smokes skip unless
  `FACTOR_SCOPE_LIVE=1`). The `store` extra (duckdb) is installed by CI + `make setup`.
- Fixtures are regenerated, not hand-edited: `uv run python scripts/gen_fixtures_phase2.py`
  (deterministic + seedless, so the artifact stays byte-for-byte reproducible).
