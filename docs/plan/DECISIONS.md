# DECISIONS (ADR-lite)

One line per decision so future sessions don't re-litigate. Newest at the bottom.

## D1 — Data: fixtures-first, live opt-in
Bundled sample data under `data/fixtures/` drives all tests and demos (offline, deterministic). Real
fetchers (AkShare/Baostock/FRED/EdgarTools) sit behind adapters and are exercised only via an opt-in
`--live` flag + separate integration tests. Keeps TDD fast and every phase runnable on Linux/CI.

## D2 — LLM: deterministic fake by default, real wired at its phase
An `LLMProvider` interface with a deterministic **fake** default lets the full pipeline run end-to-end
with no API keys and no paid calls in tests. Real **Claude Code headless** + **DeepSeek V4** are wired
at Phase 5, selected by config (`--provider`). CI never calls real providers.

## D3 — Entrypoint: one stable CLI, never broken between phases
`factor-scope run` is the single human-facing entrypoint. Its contract (emit schema-valid
`dashboard.json` + terminal render) holds at every phase boundary; later phases only enrich the
artifact. This is the "between two phases the system is not broken" requirement.

## D4 — Platform: cross-platform core + launchd documented
The engine is a plain CLI that runs on Linux/CI and macOS. Scheduling sits behind a thin adapter; a
**launchd** plist ships in Phase 7 as the documented Mac-mini production path (cron noted for Linux).

## D5 — Package manager: uv
Use `uv` for the venv and installs (`uv venv`, `uv pip install -e ".[dev]"`, `uv run ...`). The
Makefile and CI use uv. (Confirmed by the user.)

## D6 — Build backend: hatchling
`pyproject.toml` uses hatchling with an explicit `packages = ["factor_scope"]` so the editable
install works without guessing the layout.

## D7 — Store: one append-only `Reading` log, generic `(series, key, as_of, fetched_at, payload)`
Every source writes the same row shape into one DuckDB table rather than a table-per-source. The
point-in-time read (`read_as_of`: latest per key with `as_of ≤ D`) and the append-only invariant are
then defined once and shared by all adapters; payloads stay free-form JSON so a new source needs no
schema migration. `DuckDBStore` is the default backend behind a `PointInTimeStore` protocol, so a
bitemporal engine (ArcticDB, graduate tier) can swap in later. Positions are stamped with the run's
`as_of` (the file is the source; no marketplace API exists — spec §04); other sources carry their own.

## D8 — Graph engine: embedded on-disk graph persisted in DuckDB, behind a `GraphStore` interface
The §05 look-through is **exact set arithmetic** over quarterly holdings snapshots (principle #6) —
a point-in-time join + weighted sum, not variable-hop traversal. So the default `GraphStore` backend
materialises the `(:Fund)-[:HOLDS{weight,as_of}]->(:Security)` graph as a durable, append-only edge
table in **DuckDB** (the `store` extra we already ship): on-disk, offline, deterministic on Linux/CI,
and point-in-time at query time (the same `QUALIFY` latest-as-of pattern as the readings store) — a
**durable on-disk graph, never an in-memory rebuilt-each-run one** (principle #6). A graph-native
engine (**Kùzu** embedded / **Neo4j Community** in production) is documented as the swap-in behind the
`GraphStore` Protocol, to add only when fuzzy second-order / variable-hop traversal (Phase 6+) earns
the operational weight of a native binary. EDGAR 13F (US lead chain) is *not* loaded into the book
graph in Phase 3 — it carries shares, not portfolio weights, and a different universe than my funds;
it feeds the cross-market factor, and a separate lead-chain graph can be added later.

## Open (decide when reached)
- **Optional static-HTML view of `dashboard.json`** (matching the source design) — deferred; the
  stable contract is the JSON, so it can be added later without disruption.
