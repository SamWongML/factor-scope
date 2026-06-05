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

## D9 — Digestion: judgment providers vs the chore model; guardrails live in the orchestrator
The `LLMProvider` interface (`argue` both sides + `synthesize`) supplies only *judgment*; the hard
rules — abstain-when-blind, the trend gate cap, the scorecard's confidence channel — are enforced by
`digest.orchestrator` **on top of** whatever the provider returns, so even an overconfident real
model can never open the gate or change a state (spec §08, principle #4/#5). The descriptive fields
the artifact carries (text, evolution, flip-trigger, invalidation) are rendered deterministically by
the orchestrator from the *final* (post-guardrail) action, so they always match the shipped lean.
Judgment providers are **fake** (default, offline, the only one CI calls) and **claude_code**
(headless `claude -p`, bull/bear as `.claude/agents/` subagents). **DeepSeek is a chore model**
(reformat/summarise evidence, off the judgment path) — `digest.deepseek.DeepSeekChores`, *not* an
`LLMProvider`; `get_provider("deepseek")` is an error pointing at the real options. The scorecard's
sole channels into a lean are two confidence-only functions (`confidence_nudge` +
`dampen_for_weak_pattern`); neither can touch the action, a state, or the gate.

## D10 — Emerging funnel: a fixed *screening* scorecard, not a fitted composite; overlap reuses §05
The §07 funnel is two deterministic stages over **descriptive, point-in-time** inputs (fixtures-first,
live discovery opt-in). **Stage A** (`emerging.stage_a`) qualifies an *industry* as a sequence of hard
gates in spec order — signal strength (acceleration + breadth − crowding, with an acceleration floor),
durability (broad-adoption ∧ path-to-profit ∧ fad-resistance), lead-chain corroboration, an investable
wrapper — reporting the first failing gate so every stop is auditable. A theme must clear **all** gates
before any fund is scored. **Stage B** (`emerging.stage_b`) screens a cleared theme's candidate funds on
a fixed scorecard (methodology, overlap-with-core, cost, liquidity, tracking, concentration): each
criterion maps to a `[0,1]` sub-score against a **constant reference**, combined with **fixed
economic-priority weights** (methodology + overlap the decisive pair), ranked to a top 3. This is the
spec's own "score each on the same scorecard every time" discipline — a transparent *selection* tool
with constants chosen for economic meaning and **never tuned to returns**, so it does **not** violate
principle #1 (which forbids a fitted composite of the §03 *judgment* factors, read by the digest). The
weights/cut-points live as named constants beside the §03 band thresholds. **Overlap-with-core reuses
the Phase-3 §05 look-through** (`graph.lookthrough.look_through`) — a candidate's holdings are ingested
through the ordinary `fund_holdings` feed, so overlap is exact set arithmetic with **no new graph
logic** (principle #6); high overlap shrinks a fund's score and can drop it from the top 3. The
`emerging` list is now the funnel's output (not a hand-placed position); each surviving fund carries its
§03 states/gate (where price history exists; faint funds stay gate `unknown` → the digest abstains), the
Stage-A/Stage-B one-page comparison as `evidence`, and its overlap as §05 `connections`. The existing
digest then leans bull/bear over the shortlist and the trend-gate cap (D9) enforces do-not-chase on a
capped fund — promote ≤1.

## Open (decide when reached)
- **Optional static-HTML view of `dashboard.json`** (matching the source design) — deferred; the
  stable contract is the JSON, so it can be added later without disruption.
