# factor-scope — Data-flow architecture: assessment & target design

The durable design note for the data path that carries one night from sources → `dashboard.json` →
the frontend API. It is scoped to the two forward-looking goals: the engine **runs nightly** and is
then **exposed as an API serving a frontend**. It complements `docs/ROADMAP.md` (direction + the
`U01`–`U17` issue set) and `CLAUDE.md` (the rules) — this note is about the *shape of the data flow*
and whether it stays maintainable and scalable as the universe and the night count grow.

---

## 0. Verdict

**The spine is sound; keep it.** Append-only point-in-time store, a one-way snapshot boundary,
an immutable per-night artifact, and a read-only serving layer over those artifacts are the right
bones for a single-user nightly engine. This is an *adaptation*, not a rewrite.

Three things will not survive the move to a live, full-universe nightly serving a frontend, in
priority order:

1. **Offline and live run on two parallel codepaths** (`load_fixture` vs `fetch_live`). The path CI
   exercises is *not* the path production runs. This is precisely the "fixtures don't reflect the
   live run" risk in the request, and it is the single biggest maintainability hazard in the repo
   today. **Fix: record/replay at the source boundary — one codepath, swappable transport.**
2. **Three scalability cliffs** that are invisible at fixture scale (a dozen funds) and bite at
   full-universe scale (thousands of funds × daily NAVs × years of nights): whole-store snapshot
   hashing, full-history graph/snapshot rebuilds every run, and an O(nights) serving index that
   fully parses every artifact per request. All have bounded, incremental fixes.
3. **The two-phase boundary is only half-exercised offline.** The default offline `run`
   self-materialises its snapshot in one process, so the production *ingest → run* split is covered
   by exactly one test rather than by the whole suite.

Everything below maps onto the existing roadmap issues (`U03` snapshot boundary, `U04` temporal
edges, `U05` online flip, `U06` full universe) — no new direction, just the data-flow detail those
issues need.

---

## 1. The data flow as built today

Two phases, separated by the **snapshot boundary** (`pipeline.py:82`, the module docstring).

```
                         ┌──────────────── PHASE A: research / ingest (non-deterministic, fetches) ───────────────┐
   live sources ─┐       │  Market.gather (markets/ashare.py)        discover() (pipeline.py:146)                 │
   AkShare       │       │   ├─ AShareUniverse  → positions, fund_universe, etf_scale, holdings, …               │
   Baostock      ├──────▶│   ├─ ASharePrices    → prices (triple-source reconcile, select_reconciled)            │
   Mootdx        │       │   ├─ AShareThemes     → themes      ┌─ BERTopic + LLM (the separate weekly service)    │
   FRED / EDGAR ─┘       │   └─ macro / demand / prior-calls   └─ writes `themes` Readings                        │
                         │                  │                                                                     │
                         │                  ▼   append-only, point-in-time                                        │
                         │        ┌───────────────────────────┐     ┌──────────────────────────┐                 │
                         │        │ DuckDBStore (Readings log) │ ──▶ │ LadybugGraphStore (HOLDS) │                 │
                         │        └───────────────────────────┘     └──────────────────────────┘                 │
                         └──────────────────────────────────┬──────────────────────────────────────────────────-┘
                                                            │  the frozen snapshot (snapshot_id fingerprints it)
                         ┌──────────────────────────────────▼───── PHASE B: reason over snapshot (deterministic, NO network) ─┐
                         │  build_dashboard() (pipeline.py:687)                                                                │
                         │   readings ─▶ factors/states + 200-day gate ─▶ look-through connections ─▶ emerging funnel         │
                         │            ─▶ scorecard ─▶ bull/bear seats → calibrated lean ─▶ DashboardItem ×N                    │
                         └──────────────────────────────────┬──────────────────────────────────────────────────────────────-┘
                                                            ▼
                              out/dashboard.json  +  out/dashboards/<as_of>.json (immutable history; history.py)
                                                            │
                         ┌──────────────────────────────────▼─────────── SERVING (read-only) ──────────────────────┐
                         │  serve.create_app (serve.py): /dashboards, /dashboards/{as_of}, /dashboards/latest        │
                         │  index derived live from the history files (history.read_index) → frontend               │
                         └────────────────────────────────────────────────────────────────────────────────────────┘
```

**What makes it deterministic** (and must be preserved): `as_of` drives everything, never the wall
clock; `fetched_at_for(as_of)` is a derived stamp (`ingest/base.py:21`); `snapshot_id` fingerprints
the frozen read set (`store.py:146`); the `fake` provider replaces the LLM; the derived
`debate_cache`/`calls` series are excluded from the fingerprint so a re-run is byte-stable
(`pipeline.py:728`). These guarantees are good and the target design keeps every one of them.

**Persistence & serving are already well-decoupled.** The nightly writes; the API only reads
immutable history files. A recorded night never changes (`history.record` is first-write-wins,
`history.py:37`), so dated responses are cacheable forever (`serve.py:26`). This separation is a
strength — keep it.

---

## 2. The central problem: fixtures and live are two different programs

This is the part of the request that matters most: *the offline path should reflect the live run.*
Today it structurally cannot, because **each adapter is two functions** and CI only ever runs one.

Concretely, every live adapter has the shape (`ingest/prices.py` is representative):

```python
def parse(text, *, fetched_at): ...                 # shared CSV → Reading
def load_fixture(path, *, fetched_at):              # ← the OFFLINE path (tested)
    return parse(path.read_text(), fetched_at=...)
def fetch_live(code, *, fetched_at):                # ← the LIVE path  (# pragma: no cover)
    frame = ak.fund_etf_hist_em(symbol=code, ...)   # AkShare dataframe: 日期 / 收盘
    return [Reading(as_of=str(last["日期"]), payload={"nav": float(last["收盘"]), ...})]
```

The fixture is a **clean, domain-shaped CSV** (`code,as_of,nav`). The live response is a **raw
AkShare dataframe with Chinese columns** (`日期`, `收盘`). The translation `日期/收盘 → as_of/nav`
lives **only inside `fetch_live`, which is `# pragma: no cover`** — *every* live adapter is marked
that way (16 of them; `grep -c "pragma: no cover" factor_scope/ingest/*`). So the code that actually
runs in production — endpoint shapes, column names, dataframe quirks, the universe iteration in
`AShareUniverse.gather` (`markets/ashare.py:80`), the triple-source reconciliation
(`select_reconciled`) — is the code the suite never touches. The fixtures validate a *parallel
program* that exists only for tests.

The divergences, enumerated:

| Aspect | Offline (tested) | Live (production, `# pragma: no cover`) |
|---|---|---|
| **Adapter codepath** | `load_fixture` → `parse` over a clean CSV | `fetch_live` → raw vendor response + bespoke field mapping |
| **Snapshot boundary** | `run` self-materialises the snapshot in-process (`pipeline.py:707`) — ingest+reason collapse | `ingest` then `run`; `run` over an empty live store **refuses** (`SnapshotError`) |
| **Themes** | `themes.csv` loaded directly (`AShareThemes`) | yields `[]`; themes come from the separate `discover` service → the discovery→mapping→funnel chain never runs end-to-end offline |
| **Prior calls** | `calls.csv` seeds the scorecard (`_gather_prior_calls`) | accumulate from real nightly leans; bootstraps empty |
| **Prices** | one `prices.csv`, no reconciliation | three sources reconciled + a divergence circuit-breaker (`_check_price_health`) — the divergence branch is barely covered |
| **`fetched_at`** | `fetched_at_for(as_of)` (deterministic) | also `fetched_at_for(as_of)` — so even live, "when we pulled it" is synthetic, not the real fetch time |

**Why it matters for a nightly-then-serve system.** The adapters are the *most-changed, highest-risk*
code (vendor endpoints drift, AkShare renames columns, an exchange changes a field), and they are the
*least-tested*. `make check` — "THE bar" (`CLAUDE.md`) — is green while a real AkShare schema change,
a reconciliation bug, or a discovery-format mismatch sails straight through. The fixtures give a false
sense of coverage precisely on the surface most likely to break a real night.

---

## 3. Scalability assessment (full universe × years of nights)

At fixture scale these are free; at `U06` scale (the *full* CN fund universe — thousands of funds,
each with holdings + daily trading activity, accumulating nightly) they are the cliffs.

1. **`snapshot_id` hashes the entire store every run.** `store.py:146` selects *every* reading with
   `as_of <= D`, orders, serialises, and SHA-256s it — **O(total store), and the store only grows.**
   Night 1000 re-hashes 1000 nights of every fund's NAV to fingerprint one night. → *Per-series
   incremental digests* (below).
2. **`read_as_of` is a window function over the whole series** (`store.py:114`, `QUALIFY
   row_number() … PARTITION BY key`). DuckDB is fast, but it is unbounded and append-only with no
   compaction — the "latest as-of per key" scan grows with history. → periodic **compaction** of cold
   readings to Parquet (the `export_parquet` seam already exists, `store.py:159`) with the live table
   holding only the warm tail.
3. **The graph is rebuilt from full history every ingest.** `build_graph_from_store` reads
   `store.history("fund_holdings")` — *every quarter ever* — and re-MERGEs all edges each run
   (`graph/store.py:217`). Idempotent (good, `U04` landed) but O(all-history) per night. → add only
   the night's *new* disclosures.
4. **The serving index fully parses every artifact per request.** `history.read_index`
   (`history.py:51`) globs `*.json` and `Dashboard.model_validate_json` on **every** night just to
   emit `(as_of, generated_at, snapshot_id, n_items)`. `/dashboards` is **O(nights × artifact
   size)** on every call, with no caching beyond an HTTP `no-cache`. At hundreds of nights this is a
   multi-MB parse per page load. → a persisted lightweight index line per night.
5. **Single-file DuckDB + single-file Ladybug.** Correct for a single-user local box; just note the
   ceiling and that `U17`'s ArcticDB/Parquet path is the escape hatch if the box is outgrown. No
   action now (YAGNI).

None of these require a new database or a streaming stack. They are all "compute incrementally and
cache the cold part."

---

## 4. Target architecture

### 4.1 One principle: swap the *transport*, not the *flow*

Replace the `load_fixture` / `fetch_live` fork with a **single fetch path** that goes through an
injected **source transport**. Offline and live then run *the same parsing, reconciliation, universe
iteration, and discovery* — they differ only in where bytes come from.

```
            adapter.fetch(request) ──▶ ┌─────────────────────────┐ ──▶ parse() ──▶ Reading[]
                                       │   SourceTransport        │     (ONE shared codepath,
                                       │  • LiveTransport (http)  │      exercised in BOTH modes)
                                       │  • ReplayTransport (cassette) │
                                       │  • RecordingTransport (tee)   │
                                       └─────────────────────────┘
```

- **Live** = `LiveTransport`: the real HTTP / AkShare / library calls.
- **Offline** = `ReplayTransport`: serves **recorded real responses** ("cassettes") from disk, and
  **raises on a cassette miss** so drift is loud, not silent.
- **Record** = `RecordingTransport`: a `--record` run against live that tees every real response to
  a cassette, which is then committed as the new fixture.

This is the VCR / golden-snapshot / recorded-cassette pattern — the standard answer to "make tests
reflect production." The crucial shift is **where the fixture sits**: today it sits at the *clean
domain boundary* (a `nav` CSV), so the vendor-shaped translation is untested; a cassette sits at the
*network boundary* (the raw AkShare dataframe / JSON), so `fetch`'s field mapping, the Chinese column
names, the reconciliation, and the universe loop all run under the cassette in CI.

**The fixtures are recordings of reality, not hand-authored ideals.** A handful of synthetic
fixtures stay — but only for *targeted* unit cases (a deliberately divergent NAV to exercise the
circuit-breaker, a stale reading to exercise `valid=False`), clearly separated from the recorded
cassettes that drive the end-to-end run. The default offline run replays real shapes.

### 4.2 Honest clock, honest boundary

- **Decouple `fetched_at` from `as_of`.** Inject a `Clock` into `Config`: offline uses a frozen
  clock (so the artifact stays byte-for-byte), live uses the real wall clock for `fetched_at` —
  restoring honest provenance ("when we pulled it") without touching the artifact, which already
  derives `generated_at` from `as_of` (`pipeline.py:755`) and must stay clock-free. The store's
  idempotent dedup keys on the full row including `fetched_at`, so this also stops a same-`as_of`
  re-fetch on a different real day from silently colliding.
- **Make the offline default exercise the real two phases.** The standalone-convenience branch in
  `build_dashboard` (`pipeline.py:707`) should call the *same* `ingest()` (through the replay
  transport) rather than a special inline `gather`. Then `make check` covers the production
  *ingest → run* split everywhere, not just in `test_snapshot_boundary`.
- **Wire discovery into the offline flow.** Drive `discover()` from a *recorded* text corpus so the
  discovery → mapping → funnel chain runs in CI, instead of loading a pre-baked `themes.csv` that
  short-circuits it. Keep one tiny synthetic theme only for the funnel's unit tests.

### 4.3 Naming the layers (already a medallion — make it explicit, in domain terms)

The store already separates concerns; naming the boundary keeps each step additive (the `CLAUDE.md`
invariant that "each layer only *adds* to the artifact"):

- **Raw disclosures** — the append-only `Readings` log (vendor facts as pulled). *Bronze.*
- **Derived facts** — `theme_map`, the HOLDS graph, factor states + gate. Computed, dated, cached as
  their own series/edges. *Silver.*
- **The artifact** — `dashboard.json`. *Gold.*

No structural change — the recommendation is just to keep derived series clearly tagged and excluded
from the snapshot fingerprint (as `calls` / `debate_cache` already are), so the bronze layer alone
defines reproducibility.

### 4.4 Scalability changes (each independently shippable)

- **Incremental `snapshot_id`.** Maintain a per-`(series, as_of)` content digest; the night's
  snapshot id = hash of the set of series-digests with `as_of <= D`. O(series changed tonight), not
  O(whole store). Same value as today, computed incrementally.
- **Incremental graph build.** Add only the night's new holdings disclosures (those with this run's
  `as_of`); the temporal-window logic (`_window_edges`, `graph/store.py:185`) already closes prior
  windows correctly. Idempotent MERGE makes a re-run a no-op.
- **Compaction.** Periodically roll cold readings to Parquet via the existing `export_parquet` seam
  and keep only the warm tail live; reads union the two. Preserves append-only semantics.
- **Persisted serving index.** Have `history.record` append one line to an `index.jsonl`
  (`as_of, generated_at, snapshot_id, n_items`) so `read_index` reads N small lines instead of
  parsing N full artifacts. Keep the file-derived scan as a rebuild/repair fallback (the
  "no manifest to drift" property is nice — make the persisted index a *cache* of it, regenerable
  from the files). Set the per-night `ETag` to `snapshot_id` (artifacts are already immutable, so
  this is a free, strong validator).
- **Optional: static export for the frontend.** Because each night is an immutable JSON file, a tiny
  publish step can push the history to object storage / a CDN and let the frontend read static JSON
  directly — taking the server out of the hot path entirely. This fits the "one immutable artifact
  per night" model perfectly and is the cleanest way to scale reads. Defer until a real frontend
  exists (`U16`).

### 4.5 What to deliberately NOT do (YAGNI; the `CLAUDE.md` rules)

- No streaming / kappa / Kafka, no multi-writer DB, no service mesh — a single-user nightly batch
  does not need any of it, and adding it violates the local-first, deterministic mandate.
- No speculative multi-market machinery beyond the existing `Market` / `*Source` protocols.
- No change to the hard rules: states-not-composites, the trend-gate cap in the orchestrator,
  degrade-don't-raise, append-only, no wall clock in the artifact path. The target design *reinforces*
  these, it does not bend them.

---

## 5. Migration plan (incremental, test-first, mapped to the roadmap)

Each step is one session → one PR → `make check` green, and the entrypoint stays runnable throughout.

| # | Step | Touches | Maps to | Acceptance |
|---|---|---|---|---|
| 1 | Introduce `SourceTransport` + injected `Clock`; convert **one** adapter (`prices`) to fetch-through-transport with record/replay | `ingest/base.py`, `ingest/prices.py`, `config.py` | `U05` | The offline price path runs `fetch` (not `load_fixture`) over a recorded cassette; live and offline share the codepath; suite green |
| 2 | Convert the remaining adapters; record cassettes; add **contract tests** (each adapter parses its recorded payload into the expected Readings) | `ingest/*`, `markets/ashare.py`, `tests/integration` | `U05`, `U06` | No live adapter remains `# pragma: no cover` for parsing; a schema drift fails a contract test |
| 3 | Unify the boundary: offline `run` ingests via the replay transport; wire `discover` onto a recorded corpus; retire `themes.csv` as the primary path | `pipeline.py`, `markets/ashare.py` | `U03`, `U09` | The default offline run exercises *ingest → run* and discovery → mapping → funnel end-to-end |
| 4 | Incremental `snapshot_id` + incremental graph build + cold-readings compaction | `store.py`, `graph/store.py` | `U04`, `U06` | Snapshot id byte-identical to today; per-night cost is O(night), not O(history) |
| 5 | Persisted `index.jsonl` + `ETag=snapshot_id`; (optional) static export job | `history.py`, `serve.py` | `U16` | `/dashboards` is O(1) parse per request; per-night responses carry a strong validator |

Critical-path ordering note: steps 1–3 are the maintainability fix (the request's core ask) and
should land before `U06` floods the store with the full universe; steps 4–5 are the scalability fix
and are only urgent once the universe and night count are real.

---

## 6. Risks & trade-offs

- **Cassette staleness.** Recorded responses can age relative to a vendor that changed its schema.
  Mitigation: a periodic (manual or scheduled) `--record` refresh + the contract tests, which turn a
  drift into a *red test* instead of a silent production break — strictly better than today, where
  drift is invisible until a night fails.
- **Cassette size in git.** Raw vendor payloads are larger than clean CSVs. Mitigation: record the
  *minimal* response each adapter consumes (a few funds, not the whole universe) for the default
  run, and keep the full-universe recording out of the default suite (an opt-in `integration` marker).
- **One-time refactor cost across ~16 adapters.** Real, but mechanical and incremental (step 2 is
  per-adapter, each its own small PR), and it deletes the parallel `load_fixture` program rather than
  adding a third one.
- **Determinism must hold throughout.** Every step keeps the snapshot fingerprint and the
  fake-provider boundary; the frozen `Clock` replaces the only remaining wall-clock coupling.

---

## 7. Appendix — current data-flow reference

| Stage | Entry point | File |
|---|---|---|
| Ingest (Phase A) | `ingest()`, `discover()` | `pipeline.py:120`, `pipeline.py:146` |
| Market composition | `AShareMarket.gather` → `ComposedMarket` | `markets/ashare.py:180`, `markets/base.py:57` |
| Adapters (live/fixture fork) | `parse` / `load_fixture` / `fetch_live` | `ingest/*.py` |
| Point-in-time store | `DuckDBStore`, `read_as_of`, `snapshot_id` | `store/__init__.py:80` |
| Connection graph | `LadybugGraphStore`, `build_graph_from_store` | `graph/store.py` |
| Reason over snapshot (Phase B) | `build_dashboard()` | `pipeline.py:687` |
| Theme→fund mapping (derived) | `_materialise_mapping` | `pipeline.py:503` |
| Emerging funnel | `_build_emerging`, `run_funnel` | `pipeline.py:625`, `emerging/funnel.py` |
| Seats → lean | `_attach_leans`, `digest_item` | `pipeline.py:377` |
| Artifact + history | `run()`, `history.record` | `pipeline.py:759`, `history.py:37` |
| Serving | `create_app`, `read_index` | `serve.py:30`, `history.py:51` |
| Determinism stamps | `fetched_at_for`, `_resolve_as_of` | `ingest/base.py:21`, `pipeline.py:91` |
</content>
</invoke>
