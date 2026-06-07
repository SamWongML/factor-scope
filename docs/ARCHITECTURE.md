# ARCHITECTURE

The spine is the **contract**; everything reads/writes `dashboard.json`.

## Package map (`(✓)` = exists, `( )` = planned)
```
factor_scope/
  __init__.py        (✓) version
  config.py          (✓) Config: source (fixtures|live), as_of, store_path, output_path, provider
  contract/          (✓) pydantic models for dashboard.json + JSON-schema export   [the spine]
  pipeline.py        (✓) ingest(config) fills the store; build_dashboard/run read it → Dashboard
  render.py          (✓) terminal render of a Dashboard (the review surface)
  cli.py             (✓) typer app: `run`, `ingest`, `schema`  (the stable entrypoint)
  ingest/            (✓) adapters: positions, prices, fund_holdings, fred, edgar (fixture|live)
  store/             (✓) point-in-time DuckDB store (append-only); GraphStore interface
  factors/           (✓) the 8 descriptive states + trend gate (pure functions; no composite)
  graph/             (✓) build edges + deterministic look-through
  scoring/           (✓) call log, mechanical scorer, Brier/BSS scorecard
  digest/            (✓) LLMProvider (fake|claude_code|deepseek) + bull/bear/synthesis
  emerging/          (✓) Stage-A qualify + Stage-B fund screen → top 3
data/fixtures/       (✓) committed sample data (positions.csv, prices.csv, …, manifest.json)
tests/ unit|integration|system   (✓)
```

## The point-in-time store (`factor_scope/store`)
- Every ingested fact is a `Reading` — `(series, key, as_of, fetched_at, payload)` — appended to an
  **append-only** DuckDB log (`PointInTimeStore` protocol; `DuckDBStore` is the default backend,
  file or `:memory:`). No update/delete exists, so a later disclosure never rewrites an earlier read.
- `read_as_of(series, D)` returns, per key, the latest row with `as_of <= D` (point-in-time);
  `history(series[, key])` is the full audit trail. `export_parquet` writes the cold format.
- `factor-scope ingest` fills a durable store; `factor-scope run` reads it. A fixtures `run` against
  an empty store auto-ingests first, so the entrypoint still works standalone.

## The contract (`factor_scope/contract`)
- `Dashboard` — `schema_version`, `as_of`, `generated_at`, `items[]`; `by_list(name)` helper.
- `DashboardItem` — `item`, `list` (JSON key; Python attr `list_name`), `states[]`, `lean`,
  `evolution`, `flip_trigger`, `invalidation`, `connections[]`, `connections_flag`, `scorecard`,
  `evidence[]`, `gate`, `gain` (per-item return vs cost basis). Defaults make an under-construction
  item valid (keeps the entrypoint green).
- `FactorState` — `factor`, `level` (`Band`), `direction`, `evidence`, `valid`. **No composite.**
- `Connection` — `shared`, `also_in[]`, `lookthrough_wt`.
- `Lean` — `action` (`LeanAction`), `confidence` (0..1), `text`.
- `Scorecard` / `ReliabilityBucket` — Brier, skill-vs-baserate, reliability table, weak patterns.
- `Evidence` — `src`, `as_of`, `one_line` (fetch-don't-recall).
- Enums: `ListName`, `Band`, `GateState`, `LeanAction` (all `StrEnum`).

Export the JSON schema with `factor-scope schema` (or `dashboard_json_schema()`).

## The factor battery (`factor_scope/factors`)
- `bands` — `percentile_rank` (mid-rank, 0..1) + `rank_to_band` on **constant** 5/25/75/95
  cut-points. Economic meaning, never tuned to P&L: that is "states, not a composite".
- `window` — point-in-time series helpers over the store (`history` filtered to `as_of <= D`):
  `price_navs`/`fred_values`, horizon returns, rolling realised vol, drawdown, the 200-day MA.
- `battery` — the 8 states as pure `(FactorContext) -> FactorState` functions, plus
  `compute_gate`. Each ranks one reading against its **own** history → a `Band` + a risk `direction`
  + dated `evidence` + a `valid` flag; a stale/short/missing input → `valid=False` (never raises,
  never dropped). Data-backed today: trend gate, reversal, low-vol/drawdown, the macro dial; the
  other four are present-but-invalid until their sources land. **No weighted composite is formed.**
- The 200-day trend gate is the one hard cap: below the MA → `capped`, above → `open`, too little
  history → `unknown`. The lean is capped under `capped`; nothing may open it.

## Data flow (one run)
`cli.run` → `Config` → `pipeline.run`:
1. ingest adapters → point-in-time store; read it as-of the run date → items + evidence + gain.
2. attach factor `states[]` + compute `gate`.
3. attach `connections[]` from the look-through; set `connections_flag`.
4. attach the rolling `scorecard`.
5. digest (bull/bear → synthesis) → `lean` + evolution + flip_trigger + invalidation; gate
   enforced; abstain-when-blind.
6. populate the `emerging` list (Stage-A → Stage-B top-3).
7. Validate → write `dashboard.json` → `render` to terminal.

Each step only *adds* to the artifact, so partial pipelines still produce a valid (sparser) dashboard.

## Determinism & point-in-time
Fixtures runs derive `generated_at` from `as_of` (no wall clock) so the artifact reproduces
byte-for-byte. The store stamps every row `as_of` + `fetched_at`, append-only — reasoning sees
only what was knowable as of the run date.
