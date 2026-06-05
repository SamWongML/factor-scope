# ARCHITECTURE

Updated as layers land. The spine is the **contract**; everything reads/writes `dashboard.json`.

## Package map (target — `(✓)` = exists, `( )` = future phase)
```
factor_scope/
  __init__.py        (✓) version
  config.py          (✓) Config: source (fixtures|live), as_of, output_path, provider
  contract/          (✓) pydantic models for dashboard.json + JSON-schema export   [the spine]
  pipeline.py        (✓) build_dashboard(config) → Dashboard; run(config) persists it
  render.py          (✓) terminal render of a Dashboard (L6 review surface)
  cli.py             (✓) typer app: `run`, `schema`  (the stable entrypoint)
  ingest/            ( ) P1  L1 adapters: prices, fund_holdings, edgar, fred, positions (fixture|live)
  store/             ( ) P1  L2 point-in-time DuckDB+Parquet; GraphStore iface + impls
  factors/           ( ) P2  L3 the 8 descriptive states + trend gate (pure functions)
  graph/             ( ) P3  build edges + deterministic look-through
  scoring/           ( ) P4  call log, mechanical scorer, Brier/BSS scorecard
  digest/            ( ) P5  LLMProvider (fake|claude_code|deepseek) + bull/bear/synthesis
  emerging/          ( ) P6  Stage-A qualify + Stage-B fund screen → top 3
data/fixtures/       (✓) committed sample data (items.json)
tests/ unit|system   (✓) (+ integration/ in P1)
```

## The contract (`factor_scope/contract`)
- `Dashboard` — `schema_version`, `as_of`, `generated_at`, `items[]`; `by_list(name)` helper.
- `DashboardItem` — `item`, `list` (JSON key; Python attr `list_name`), `states[]`, `lean`,
  `evolution`, `flip_trigger`, `invalidation`, `connections[]`, `connections_flag`, `scorecard`,
  `evidence[]`, `gate`. Defaults make an under-construction item valid (keeps the entrypoint green).
- `FactorState` — `factor`, `level` (`Band`), `direction`, `evidence`, `valid`. **No composite.**
- `Connection` — `shared`, `also_in[]`, `lookthrough_wt`.
- `Lean` — `action` (`LeanAction`), `confidence` (0..1), `text`.
- `Scorecard` / `ReliabilityBucket` — Brier, skill-vs-baserate, reliability table, weak patterns.
- `Evidence` — `src`, `as_of`, `one_line` (fetch-don't-recall).
- Enums: `ListName`, `Band`, `GateState`, `LeanAction` (all `StrEnum`).

Export the JSON schema with `factor-scope schema` (or `dashboard_json_schema()`).

## Data flow (one run)
`cli.run` → `Config` → `pipeline.run`:
1. (P1) ingest → point-in-time store; (P0) read fixture item list.
2. (P2) attach factor `states[]` + compute `gate`.
3. (P3) attach `connections[]` from the look-through; set `connections_flag`.
4. (P4) attach the rolling `scorecard`.
5. (P5) digest (bull/bear → synthesis) → `lean` + evolution + flip_trigger + invalidation; gate
   enforced; abstain-when-blind.
6. (P6) populate the `emerging` list (Stage-A → Stage-B top-3).
7. Validate → write `dashboard.json` → `render` to terminal.

Each step only *adds* to the artifact, so partial pipelines still produce a valid (sparser) dashboard.

## Determinism & point-in-time
Fixtures runs derive `generated_at` from `as_of` (no wall clock) so the artifact reproduces
byte-for-byte. The store (P1+) stamps every row `as_of` + `fetched_at`, append-only — reasoning sees
only what was knowable as of the run date.
