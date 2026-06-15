# Data-flow architecture — scaling the nightly engine to a durable, served system

This is the durable companion to `CLAUDE.md` and `docs/ROADMAP.md` for **one question**: as the engine
moves from a hand-run nightly batch to an **automatically-triggered nightly job whose history is
exposed as an API to a frontend**, and as the dataset grows from a 3-code fixture to the **full CN
fund universe over years**, does the current data flow hold — and if not, what is the smallest,
most durable change that makes it hold?

**Verdict.** The *conceptual* data flow is already a modern, best-practice design — a medallion
(bronze → silver → gold) lakehouse with a one-way snapshot boundary that makes it a reproducible,
replayable pipeline. **Do not rewrite it.** What does not scale is the *physical representation* of
three layers: the readings log's table shape and whole-history scans, the graph's full rebuild each
night, and the serve layer re-parsing every artifact on every list request. All three are fixable
behind the `Protocol` seams the codebase already exposes (`PointInTimeStore`, `GraphStore`,
`Market`), so this is an **evolution by swap, not a rewrite**, and every hard invariant survives
untouched.

---

## 1. The current data flow, as a medallion

The engine already maps cleanly onto the bronze/silver/gold pattern that is the 2025 industry
default for batch-to-serving systems ([Databricks][db-med], [Microsoft Learn][ms-med]):

| Medallion layer | factor-scope | Artifact |
|---|---|---|
| **Bronze** — raw, dated, append-only | `ingest/` → `store/` `readings` log; `graph/` `HOLDS` edges | `Reading(series, key, as_of, fetched_at, payload)` |
| **Silver** — cleaned, conformed, derived | `factors/` (8 states + trend gate), `graph/lookthrough.py`, `emerging/` funnel, `scoring/` scorecard | `FactorState`, `Connection`, shortlists |
| **Gold** — business-ready, served | `digest/` debate → `contract/` `Dashboard` | `out/dashboard.json` + immutable `out/dashboards/<as_of>.json` |
| **Serving** | `history.py` + `serve.py` | read-only FastAPI over the gold history |

The **snapshot boundary** (`U03`, landed) splits a non-deterministic *ingest* (fetch → write dated
Readings) from a deterministic *reason-over-snapshot* (`build_dashboard`), reconciling
online-by-default with byte-for-byte reproducibility. This is exactly the "replayable data pipeline"
pattern from the reproducibility literature — pin code + a content-addressed data snapshot and any
run reproduces exactly ([Bauplan/Nessie replayable pipelines][bauplan]; [DVC snapshot IDs][lakefs]).

---

## 2. What is already best-practice — keep it

These are not just adequate; they are the parts a redesign must preserve, because they are *why* the
system is trustworthy. Each is a hard invariant in `CLAUDE.md`.

- **Append-only, point-in-time bronze.** Every fact is a dated `Reading`; `read_as_of(series, D)`
  returns only what was knowable on `D`. This is the bitemporal store that quant-research systems
  converge on for reproducibility and no-look-ahead ([ArcticDB on bitemporal quant data][arctic-quant]).
- **One-way snapshot boundary + content fingerprint.** `snapshot_id` records *which* frozen state a
  run read; two runs over identical knowable-by-`D` facts share an id. This is the data-snapshot id
  every reproducible-ML guide asks for.
- **Fixtures share the live code path.** Below `ingest`, nothing branches on fixtures-vs-live; both
  `load_fixture` and `fetch_live` return `list[Reading]` into the *same* store, and the deterministic
  `fake` provider stands in for the LLM. The offline suite is hermetic not by avoiding the network in
  code but by replaying a frozen snapshot — the textbook "record production → replay as a golden
  test" parity ([reproducible-ML snapshot/replay][lakefs]). **This is the single most valuable
  property to protect in the redesign** (see §7).
- **Guardrails live server-side of the model.** The trend gate, abstain-when-blind, de-bias, and
  scorecard nudge are enforced by the orchestrator on top of model output. Determinism of the
  artifact text (rendered from the final action, never the model) is preserved.
- **Swappable backends via `Protocol`.** `PointInTimeStore`, `GraphStore`, and `Market` are runtime
  protocols. The default `DuckDBStore` / `LadybugGraphStore` are *implementations*, not the contract.
  **These protocols are the seams that make everything in §5 a swap.**

---

## 3. Where it will not scale

Two growth axes are stated in the brief: **dataset size** (full universe × years) and **serving a
frontend off a growing history**. The conceptual flow is fine on both; specific *implementations* are
O(full history) or O(all nights) per run/request and will degrade super-linearly.

### 3a. Bronze: the readings table shape and whole-history scans

`store/__init__.py`:

1. **The whole JSON `payload` is in the PRIMARY KEY** (`PRIMARY KEY (series, key, as_of, fetched_at,
   payload)`) and stored as an opaque `VARCHAR`. Every insert maintains an index over a full JSON
   string; comparisons are O(payload length). The dedup intent is right; the mechanism should key on
   a cheap **content hash**, not the literal payload.
2. **`payload` as opaque JSON** forecloses columnar predicate pushdown and typed compression. A read
   cannot skip row-groups on a payload field; it must materialise rows and parse JSON in Python.
3. **`read_as_of` window-scans the entire series every night.** `QUALIFY row_number() OVER (PARTITION
   BY key ORDER BY as_of DESC …)` has no `as_of` pruning. For one code that is cheap; for the full
   universe over years (millions of rows per series) it re-scans the whole series each of the
   thousands of times a night calls it.
4. **`snapshot_id` pulls *all* readings `≤ D` into Python and hashes them** (`fetchall()` →
   `json.dumps`). This is the worst offender: it is O(entire knowable history) **per run**, in Python
   memory. A 3-year, full-universe store is tens of millions of rows; hashing all of them every night
   to produce one id is untenable.
5. **One mutable DuckDB file = one writer.** DuckDB permits a single read-write process; a second
   writer (or a concurrent analytical reader holding the file) contends. The nightly job writing while
   anything else reads the store is a lock fight. (`serve` happens to read the *artifacts*, not the
   store, so it is safe today — but any store-backed API or dashboard would collide.)

### 3b. Silver graph: full rebuild each night

`graph/store.py` → `build_graph_from_store` reads the **full** `history("fund_holdings")` and
`history("edgar")` and recomputes every validity window on **every** ingest. The `MERGE` is
idempotent so the writes are cheap, but the *read + window computation* is O(full history) nightly and
grows without bound.

### 3c. Gold serving: re-parsing every night on every list

`history.py` → `read_index` **opens and fully parses every `<as_of>.json` in the directory on every
`/dashboards` request** (and `/dashboards/latest` calls it too). After one year that is ~365 full
Pydantic validations per list call; after five years, ~1,800 — uncached, on a hot endpoint. The
per-night dated endpoint is correctly `immutable`-cached, but the index and `latest` are
`no-cache` with **no ETag / conditional GET**, so a frontend cannot get a cheap `304`. As the
universe grows, each artifact also grows, with **no field projection or pagination within a night**.

---

## 4. What best practice says (the research)

- **Medallion is the default batch-to-serve shape**, and the bronze layer should be *append-only with
  metadata, schema-on-read in an open columnar format (Parquet/Delta/Iceberg)* rather than opaque
  blobs — exactly the gap in §3a.2 ([Databricks][db-med], [Microsoft][ms-med], [Strengholt][med-bp]).
- **DuckDB scales into the "forgotten middle" (≈10 GB–100 TB) on one machine**, but the unit of
  scale is **partitioned Parquet read in place**, not a monolithic file: partition pruning + row-group
  skipping cut peak memory **up to 8×**, and the failure modes are the *monolith* (OOM) and the
  *tiny-files* trap (40k fragments) — so partition deliberately by series + time
  ([DuckDB partitioned Parquet][duckdb-part], [MotherDuck partitioned writes][md-part]).
- **DuckLake** (DuckDB team, 2025) puts catalog/snapshot metadata in a small SQL DB and table data in
  Parquet, giving **ACID, snapshots, and time-travel** with a *single writer + many concurrent
  readers* and no manifest/catalog service — a near-exact fit for "one nightly writer, a read-only
  API, growing history, and a snapshot boundary you already think in." It is young (v0.x) and imports
  from/to Iceberg, so it is a safe forward bet, not a lock-in ([DuckLake vs Iceberg/Delta][dlh-otf],
  [MotherDuck on catalogs][md-catalog]).
- **Reproducibility = pinned code + a content-addressed data snapshot**, which the engine already has
  in `snapshot_id`; the lakehouse formats above give the same thing *natively* as a snapshot id, so
  the boundary gets cheaper, not different ([Bauplan/Nessie][bauplan], [DVC/lakeFS][lakefs]).
- **Serving precomputed immutable artifacts** wants `Cache-Control: immutable` for dated resources
  (already done) **and ETag + conditional GET on the moving index/latest**, plus cursor/keyset
  pagination for growing lists ([ETag conditional requests][etag], [immutable caching][moz-immut],
  [pagination best practice][knit-page]). `snapshot_id` is a ready-made strong ETag.
- **ArcticDB** is the graduate tier for *billions* of rows of bitemporal time-series — real, but
  overkill for a single-user nightly engine until the universe is genuinely that large; it stays the
  `U17` deferred option, not the near-term target ([ArcticDB][arctic-quant]).

---

## 5. The optimal adaptation — a partitioned lakehouse on the existing spine

Keep every contract and invariant; change only the physical representation behind the protocols.

### 5.1 Bronze — typed, content-addressed, partitioned

- **Re-key dedup on a content hash, not the payload.** PK / `ON CONFLICT` becomes
  `(series, key, as_of, fetched_at, payload_sha)` where `payload_sha = sha256(canonical_json)`. Same
  idempotency, no full-string index. (Pure storage change; `Reading` is unchanged.)
- **Partition the cold log as Hive-partitioned Parquet** under `store/series=<series>/year=<yyyy>/…`,
  written from the existing `export_parquet` formalised into a partitioned `COPY … (FORMAT PARQUET,
  PARTITION_BY (series, year))`. Recent partitions stay hot (DuckDB or DuckLake); cold ones are read
  in place with predicate pushdown. This is the §4 8× win and directly attacks §3a.2–3.
- **Make `read_as_of` prune by time.** Carry an `as_of`/partition predicate into the scan so a night
  reads only the partitions it needs, and add the `(series, as_of)` ordering the partition layout
  provides. The `QUALIFY` stays; it now runs over a pruned slice, not the whole series.
- **Make `snapshot_id` incremental and in-engine.** Two compatible options, pick one:
  1. **Per-day rolling digest.** Maintain a small `series_day_digest(series, as_of, digest)` table; a
     night's `snapshot_id` is a fold over `digest` for `as_of ≤ D`, never a full-history Python load.
  2. **Hash in DuckDB.** Compute the fingerprint with a SQL hash aggregate so rows never leave the
     engine. If the backend becomes DuckLake, take its native snapshot id directly.
  Either removes the O(history) Python hash while keeping the *exact* "content of everything knowable
  by `D`" semantics.
- **Adopt DuckLake as the durable target** behind `PointInTimeStore`: catalog in a tiny
  SQLite/DuckDB, data as the partitioned Parquet above, giving ACID + native snapshots/time-travel +
  **single-writer / many-reader** (the nightly job commits; the API and any analytics read committed
  snapshots without a lock fight, §3a.5). Because it is a `PointInTimeStore` implementation, the
  switch is one constructor, and the partitioned-Parquet step (above) is the safe interim that needs
  no new dependency.

### 5.2 Silver graph — incremental, not rebuilt

- **Track a high-water mark** (last ingested `as_of`) and feed `build_graph_from_store` only the
  disclosures *since* it. The `MERGE` is already idempotent on disclosure identity, so closing the
  prior window and opening the new one is a bounded, per-night delta instead of an O(full-history)
  rebuild (§3b). The look-through read path is unchanged.

### 5.3 Gold serving — a manifest, ETags, and projection

- **Persist a derived index the cheap way.** Append one `DashboardIndexEntry` to a small
  `index.json` (or a tiny catalog table) **transactionally inside `record()`** when a night is first
  written; `read_index` reads that O(1) instead of parsing every artifact (§3c). The filesystem stays
  the source of truth — rebuild the manifest from disk if it is missing, so it can never *drift*, only
  lag, and a missing manifest self-heals.
- **Add ETag + conditional GET** on `/dashboards` and `/dashboards/latest`, using each night's
  `snapshot_id` (and the latest `as_of` for the index) as the strong validator, so the frontend gets
  `304`s on the moving endpoints. Dated nights keep `immutable`.
- **Project and paginate within a night.** Add optional field selection / per-list sub-resources
  (`/dashboards/<as_of>/holdings`, `?fields=…`) so a growing full-universe artifact streams to the UI
  by list rather than as one ever-larger blob, with keyset pagination over the night index.
- **Serve from object storage + CDN when it leaves localhost.** Immutable dated artifacts are already
  perfectly CDN-cacheable; this is a deployment choice, not a code change, once the manifest + ETags
  land.

### 5.4 The same backend in tests as in production

Today tests use an in-memory DuckDB while production uses a file; the new physical format (partitioned
Parquet / DuckLake) should be the **same path tests exercise**, on a tiny dataset, so the test mode
reflects the real *read* path (partition pruning, incremental graph, manifest), not a simplified one.
This is the §7 fixtures concern.

---

## 6. Phased migration (swap, not rewrite), mapped to the roadmap

Each step is independently shippable behind a protocol, keeps `make check` green, and changes no
contract. Ordered by value-to-risk.

1. **Re-key dedup on `payload_sha`** (§5.1). Pure `DuckDBStore` change; no interface change. *Quick win.*
2. **Persist the serve manifest + ETags** (§5.3). Touches `history.py` / `serve.py` only; immediately
   removes the per-request re-parse and unblocks the frontend. *Highest serving value.*
3. **Incremental graph build** (§5.2). Bounded change to `pipeline.ingest` + a high-water mark.
4. **Incremental / in-engine `snapshot_id`** (§5.1). Removes the O(history) nightly hash.
5. **Partitioned-Parquet cold tier + pruned `read_as_of`** (§5.1). New `PointInTimeStore`
   implementation; the existing one stays for small/offline runs.
6. **DuckLake backend** (§5.1) as the durable target once 1–5 are in. Aligns with — and is a lighter
   near-term answer than — `U17`'s deferred ArcticDB; reserve ArcticDB for a genuine
   billions-of-rows future.

This sequence also slots into the existing `U`-set: steps 1–4 harden `U03`/`U04` (snapshot boundary +
temporal edges) for scale; step 2 is the storage half of `U16` (the viewer's API seam); steps 5–6 are
the concrete, non-deferred subset of `U17`.

---

## 7. Fixtures: make the test mode a *recorded real night*

The brief asks that fixtures reflect the live run as closely as possible and that the design be
durable as data grows. The parity is already strong (§2); two changes make it stronger and keep it
true under growth:

- **Record → replay.** Add a capture mode that, at the snapshot boundary, writes a real live ingest's
  Readings into the fixture format (or a committed Parquet snapshot). Fixtures then *are* a frozen
  real night — the canonical "capture production, replay as a golden test" pattern — so they drift
  toward, not away from, live as sources evolve. The deterministic `fetched_at = f"{as_of}T22:00:00Z"`
  rule already makes any captured night reproducible.
- **Grow fixtures to exercise the cold path.** Today's fixtures are a single `as_of` partition and ~3
  codes — they never exercise partition pruning, incremental graph deltas, or the manifest at scale.
  Add a small **multi-`as_of`, multi-partition, larger-universe** fixture slice so the suite tests the
  *same* physical read path production uses (§5.4), still byte-for-byte deterministic. Keep the tiny
  fixture for fast unit runs; add the partitioned one for integration/system.

Net: the test mode keeps running the identical reasoning and the **identical storage/read path** as
live, just over a frozen, real, multi-partition snapshot — which is exactly "reflect the live
situation as much as possible" under a growing dataset.

---

## 8. What explicitly does not change

The append-only point-in-time semantics, the one-way snapshot boundary, `generated_at` derived from
`as_of` (no wall clock in the artifact), no-composite factors, guardrails server-side of the model,
the `Dashboard` contract, and the `Protocol` seams. Every change above is a *physical
representation* change behind those seams. That is the whole point: the data flow is already right;
this makes its storage and serving scale durably to the full universe over years and to a frontend
reading a growing history.

---

[db-med]: https://www.databricks.com/blog/what-is-medallion-architecture
[ms-med]: https://learn.microsoft.com/en-us/azure/databricks/lakehouse/medallion
[med-bp]: https://piethein.medium.com/medallion-architecture-best-practices-for-managing-bronze-silver-and-gold-486de7c90055
[duckdb-part]: https://medium.com/@hadiyolworld007/one-file-warehouse-partitioned-parquet-duckdb-11968cec33ef
[md-part]: https://motherduck.com/learn/partitioned-writes-parquet-ducklake/
[dlh-otf]: https://datalakehousehub.com/blog/2025-09-ultimate-guide-to-open-table-formats/
[md-catalog]: https://motherduck.com/videos/escaping-catalog-hell-a-guide-to-iceberg-duckdb-the-data-lakehouse/
[bauplan]: https://arxiv.org/pdf/2404.13682
[lakefs]: https://lakefs.io/blog/improve-ml-pipeline-development-with-reproducibility/
[arctic-quant]: https://medium.com/arcticdb/why-arcticdb-works-so-well-for-quant-research-and-data-science-98362ac712f0
[etag]: https://www.baeldung.com/etags-for-rest-with-spring
[moz-immut]: https://hacks.mozilla.org/2017/01/using-immutable-caching-to-speed-up-the-web/
[knit-page]: https://www.getknit.dev/blog/api-pagination-best-practices
