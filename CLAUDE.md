# CLAUDE.md — factor-scope

> Read this + `docs/plan/PROGRESS.md` + the current `docs/plan/phases/phase-N.md` at the start of
> every session. That is enough to resume. Do **not** re-read the whole spec or replay history.

## What this is
The **Wealth-Assistant Engine** — a local-first, single-user, nightly-batch decision-support engine
for an A-share / funds-and-ETFs portfolio. One run emits one dated artifact, **`dashboard.json`**
(three lists — holdings / watchlist / emerging — plus `connections[]` and a `scorecard`). A human
reviews it each morning. **The engine never places orders.**

- Full spec: `docs/spec/SPEC.md` (distilled) and `docs/spec/wealth-assistant-engine-v4.html` (source).
- Architecture + the contract: `docs/ARCHITECTURE.md`.
- The phase plan: `docs/plan/ROADMAP.md`. Locked decisions: `docs/plan/DECISIONS.md`.

## Where we are now
**Phases 0–1 done; Phase 2 (factor states + trend gate) is next.** Always confirm against
`docs/plan/PROGRESS.md` (the live board) — it carries the current phase + the exact NEXT ACTION.
The point-in-time **store** is now the seam between ingestion and the artifact: Phase 2 factor
states read `prices`/`fred` via `store.read_as_of(...)`, no new ingestion needed.

## Package manager & commands
Use **uv** (not pip/poetry).
- `make setup` — create the venv and install (`uv venv` + `uv pip install -e ".[dev,store]"`).
- `make test` — full suite, offline (unit + integration + system). Must be green to close any unit.
- `make system` — the end-to-end "nothing is broken" gate (`pytest -m system`).
- `make run` — `uv run factor-scope run --fixtures`: build + print the morning artifact.
- `make lint` / `make typecheck` — ruff + mypy (strict). `make check` runs everything CI runs.

Extras (opt-in, never required by CI's offline run): `store` (duckdb + pyarrow, installed by
`make setup`/CI), `live` (akshare, baostock, edgartools, fredapi, httpx — only for `--live`).

## CLI (`factor-scope`, defined in `factor_scope/cli.py`)
- `run [--fixtures|--live] [--as-of D] [--store-path P] [-o OUT] [--provider fake|claude_code|deepseek] [-q]`
  — build the artifact and print it. With no `--store-path`, uses an ephemeral in-memory store
  auto-ingested from the source, so it always works standalone.
- `ingest [--fixtures|--live] [--as-of D] [--store-path P]` — fill the durable point-in-time store
  (append-only) so `run --store-path P` reads it.
- `schema [-o OUT]` — print/write the `dashboard.json` JSON schema.

## The one entrypoint (keep it green)
`factor-scope run` always emits a schema-valid `dashboard.json` and prints a terminal render.
**Invariant:** from Phase 0 on, `factor-scope run --fixtures` succeeds at every phase boundary.
Later phases only *enrich* what fills the artifact; never leave the pipeline non-runnable.

## Code map (`factor_scope/`)
- `contract/` — the `dashboard.json` Pydantic models + `dashboard_json_schema()`. **The one
  contract**; every field a later phase produces already has a home here (defaults keep an
  under-construction item valid). The JSON key is `list`; the Python attr is `list_name`.
- `config.py` — frozen `Config` (source fixtures/live, `fixtures_dir`, `as_of`, `output_path`,
  `store_path`, `provider`). The knobs that make a run deterministic + point-in-time.
- `pipeline.py` — orchestrates the layers. `ingest()` fills the store; `build_dashboard()` reads it
  as-of the run date into items (positions + priced `evidence[]` + per-item `gain`); `run()` writes
  the artifact. The as-of date is the override or the fixture stamp — **never the wall clock**.
- `store/` — `Reading` + `PointInTimeStore` protocol + append-only `DuckDBStore` (file or
  `:memory:`). `read_as_of` = latest row per key with `as_of <= D`; `history` = the audit trail.
- `ingest/` — L1 adapters (positions, prices, fund_holdings, fred, edgar). Each has a deterministic
  fixture backend (default) + a lazy `fetch_live` behind `--live`. `gather_fixture_readings` /
  `gather_live_readings` are the two entrypoints.
- `render.py` — the terminal render of a `Dashboard`. `cli.py` — the Typer app.
- `data/fixtures/` — committed offline sample data (`positions.csv`, `prices.csv`,
  `fund_holdings.csv`, `fred.csv`, `edgar.csv`, `manifest.json` carrying the `as_of` stamp).
- `tests/` — split by marker: `unit/` (pure), `integration/` (store/fixtures on disk),
  `system/` (e2e `factor-scope run`). Live tests skip unless `FACTOR_SCOPE_LIVE=1`.

## Load-bearing principles (do not violate — from the spec)
1. **States, not a composite.** Each factor → a descriptive *state* (band vs its own history +
   `direction` + `valid`). No weighted composite anywhere. The LLM reads states; it never fits them.
2. **Point-in-time, everywhere.** Every reading stamped `as_of` + `fetched_at`, append-only, never
   overwritten. Holdings keyed by filing date.
3. **Fetch-don't-recall.** Numbers are pulled/dated/stamped, never recalled by a model.
4. **The trend gate is a hard rule.** Below the 200-day MA → lean capped at Hold/Avoid; nothing
   (not even the scorecard) may open it.
5. **The scorecard is descriptive only.** It may nudge confidence; it can never change a state,
   open the gate, or supply a number.
6. **Exact > clever.** Deterministic set-arithmetic look-through, durable on-disk graph; no GraphRAG,
   no in-memory rebuilt-each-run graph.
7. **One contract.** Everything reads/writes `dashboard.json` (`factor_scope/contract`).

## How to work (TDD, one cycle at a time)
1. Write a failing unit test for the next unit; confirm it fails; **commit the failing test**.
2. Implement the minimum to pass. **Do not edit the test to pass it.** Refactor green.
3. Close a phase only when `make test` + `make system` are green and `factor-scope run` still works.
4. **Update `docs/plan/PROGRESS.md`** (status + NEXT ACTION) and commit. The diff is the record.

## Conventions
- Python 3.11+, type-annotated, `mypy --strict` clean, `ruff` clean.
- Fixtures-first: tests/demos run offline on `data/fixtures/`; live sources behind `--live` only.
- Default LLM provider is a deterministic **fake**; real providers (`claude_code`, `deepseek`) are
  opt-in and never called in CI.
- Determinism: fixtures runs reproduce `dashboard.json` byte-for-byte (no wall-clock in the artifact).
