# PROGRESS — live handoff board

> Single source of truth for "where are we". A fresh session reads `CLAUDE.md` + this file + the
> current `phases/phase-N.md` and resumes. Update status + **NEXT ACTION** whenever a unit or phase
> closes, then commit. Statuses: `todo` · `wip` · `done`.

**Current phase:** Phase 2 — Factor states + trend gate
**NEXT ACTION:** Start Phase 2 RED — write `tests/unit/test_factors.py` for the descriptive factor
states (rank-against-own-history bands + `direction` + `valid`) and the 200-day trend gate, then
implement `factor_scope/factors/`. The states read from the store (`prices`, `fred`) via the same
point-in-time reads added in Phase 1. See `phases/phase-2.md`.

| Phase | Status | Closed by (commit) | System test |
|-------|--------|--------------------|-------------|
| 0 — Scaffold, contract, entrypoint, tracking | **done** | _initial Phase 0 commit_ | `make system` ✓ (7 tests) |
| 1 — Ingestion + point-in-time store | **done** | _this commit_ | `make system` ✓ (5 tests) |
| 2 — Factor states + trend gate | todo | — | — |
| 3 — Connection graph + look-through | todo | — | — |
| 4 — Self-scoring loop | todo | — | — |
| 5 — Digestion: LLM provider + bull/bear | todo | — | — |
| 6 — Emerging radar funnel | todo | — | — |
| 7 — Scheduling, packaging & ops | todo | — | — |

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
- The point-in-time store is the seam: Phase 2 factor states read `prices`/`fred` via
  `store.read_as_of(...)` — no new ingestion needed, just more history in the fixtures if a band
  needs a distribution to rank against.
- Live fetchers stay behind `--live`, lazily imported, never in CI (smokes skip unless
  `FACTOR_SCOPE_LIVE=1`). The `store` extra (duckdb) is now installed by CI + `make setup`.
- When picking the graph engine in Phase 3, record it in `DECISIONS.md` (open item D-graph).
