# PROGRESS — live handoff board

> Single source of truth for "where are we". A fresh session reads `CLAUDE.md` + this file + the
> current `phases/phase-N.md` and resumes. Update status + **NEXT ACTION** whenever a unit or phase
> closes, then commit. Statuses: `todo` · `wip` · `done`.

**Current phase:** Phase 1 — Ingestion + point-in-time store
**NEXT ACTION:** Start Phase 1 RED — write `tests/unit/test_positions_adapter.py` for the
`positions.csv` loader (parse `{code,name,cost_basis,shares,list}`), then implement
`factor_scope/ingest/positions.py`. See `phases/phase-1.md`.

| Phase | Status | Closed by (commit) | System test |
|-------|--------|--------------------|-------------|
| 0 — Scaffold, contract, entrypoint, tracking | **done** | _initial Phase 0 commit_ | `make system` ✓ (7 tests) |
| 1 — Ingestion + point-in-time store | todo | — | — |
| 2 — Factor states + trend gate | todo | — | — |
| 3 — Connection graph + look-through | todo | — | — |
| 4 — Self-scoring loop | todo | — | — |
| 5 — Digestion: LLM provider + bull/bear | todo | — | — |
| 6 — Emerging radar funnel | todo | — | — |
| 7 — Scheduling, packaging & ops | todo | — | — |

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
- Add an `integration` test dir in Phase 1; keep live fetchers behind `--live` (never in CI).
- When picking the graph engine in Phase 3, record it in `DECISIONS.md` (open item D-graph).
