# factor-scope — Serving-Path Scalability Evaluation

An **independent** assessment of whether the proposed data-flow refactor (see `docs/DATA_FLOW.md`)
can deliver data to a frontend at scale — low latency, high throughput — under load. This evaluation
is **standalone**: it is not filed under, dependent on, or scoped by any existing roadmap issue.

Scope: the *read path* the frontend touches, not the nightly write path. Where the write-path
refactor affects serving, it is called out.

---

## Conclusion

**Requires Modification.**

The architectural *direction* is correct and, in fact, is the right shape for low-latency /
high-throughput delivery: immutable, content-addressed gold artifacts (`dashboards/<as_of>.json`)
served from a read path that is decoupled from the writer. That pattern is CDN-cacheable and reads in
O(1). The write-path fixes (content-dedup, incremental ingest) are also *necessary preconditions* —
they shrink the store ~500×, which is what keeps any future DB-backed read fast.

But **as proposed, the refactor is not sufficient for high-load frontend delivery**, for three
reasons:

1. It leaves the **single worst serving bottleneck untouched**: the night-index and `latest`
   endpoints re-read and re-parse *every* archived night on *every* request, uncached. This is
   O(N-nights) per request today and degrades linearly forever.
2. It **introduces a new, concurrency-fragile surface**: read-only DuckDB/DuckLake queries in the
   request path. DuckDB is an in-process OLAP engine optimized for big scans, not for many concurrent
   low-latency lookups, and has hard write/read process-locking rules.
3. It implicitly assumes Python file-serving is enough for "high throughput," which will not hold
   without a static/CDN tier.

All three are fixable with bounded, well-understood changes (below). With them, the design is
Suitable. Without them, frontend responsiveness collapses as history grows or concurrency rises.

---

## Evidence and rationale, per serving path

### Path A — single night by date: `GET /dashboards/{as_of}`

Verified: `serve.py:67-75` reads and validates one `<as_of>.json` (`history.load` → `_read_dashboard`
→ `Dashboard.model_validate_json`), sets `Cache-Control: public, max-age=31536000, immutable`.

- **Latency:** cache miss = one ~20–40 KB file read + one Pydantic parse ≈ low single-digit ms; cache
  hit (browser/CDN) ≈ 0. Excellent.
- **Throughput:** payload is immutable and content-addressed by `snapshot_id`, so it is trivially
  cacheable → effectively unbounded behind a CDN/static tier.
- **Memory:** one small buffer per miss.
- **Verdict:** **Suitable as-is.** The refactor neither helps nor hurts this path. This is the
  dominant frontend need (open a given morning) and it is already well-built.

### Path B — index and latest: `GET /dashboards`, `GET /dashboards/latest`

Verified: both call `read_index(history_dir)` (`serve.py:50-65`), which **globs the directory and
opens + fully Pydantic-validates every `*.json` night** to build the manifest (`history.py:58-73`),
and both set `Cache-Control: no-cache`. `latest` additionally `load()`s the newest file.

- **Cost:** O(N) file opens + O(N) JSON parses **per request**, **uncached**.
- **Projected latency** (≈30 KB/night, Pydantic validation ≈ tens of µs–ms/night):

  | History | Files read+parsed/request | Bytes/request | Rough single-request time |
  |--------:|---------------------------:|--------------:|--------------------------:|
  | 3 months (~65) | 65 | ~2 MB | tens of ms |
  | 1 year (~250) | 250 | ~7.5 MB | ~100–300 ms |
  | 3 years (~750) | 750 | ~22 MB | several hundred ms–1 s+ |
  | 5 years (~1,250) | 1,250 | ~37 MB | ~1–2 s+ |

- **Under load:** every concurrent request re-does the full scan+parse (no shared cache), so aggregate
  work is O(N × concurrency). CPU-bound Pydantic parsing saturates cores; p99 latency rises with both
  history length and concurrency. A frontend that polls `latest` or renders a night picker from
  `/dashboards` hits this on first paint.
- **Memory:** each in-flight index request transiently holds the parsed content of *all* nights →
  ~N×30 KB per concurrent request (e.g. ~22 MB × 100 concurrent ≈ ~2.2 GB transient at 3 years).
- **Critically, the data-flow refactor does NOT address this** — the index is derived from JSON
  files, not from the store, so content-dedup / incremental ingest / DuckLake change nothing here.
  **This is the #1 frontend-responsiveness risk and it is independent of the refactor.**
- **Verdict:** **Not suitable as-is; must be modified** (materialize the index — see R1).

### Path C — NEW time-series endpoints: read-only DuckDB/DuckLake over silver

The refactor proposes serving "a factor's history" / "a fund's NAV trail" from a read-only
DuckDB/DuckLake connection over the silver Parquet (`DATA_FLOW.md` §5.4). This is **net-new attack
surface**, and it is the riskiest part for frontend delivery:

- **Engine fit:** DuckDB is an **in-process OLAP** engine — built for high-throughput scans/
  aggregations on a single big query, **not** for many concurrent small low-latency requests. There
  is no built-in connection pool, no admission control, and a query can claim multiple threads and
  large RAM (sorts, the `row_number() OVER (PARTITION BY key …)` window in `read_as_of`). The
  ecosystem consensus is that high-concurrency low-latency serving is the domain of ClickHouse / Pinot,
  not DuckDB.
- **Latency:** a single point-in-time read over the *post-fix* (small) silver store is ms–tens of ms.
  But a *full series* read (chart of many dates) scans wider, and over a partitioned Parquet cold
  tier it depends entirely on partition pruning; a query that misses pruning scans many files.
- **Throughput / tail latency:** under concurrent requests, queries contend for CPU cores and the
  memory budget; heavy queries trigger spill-to-disk and inflate p99. No isolation from the nightly
  job or the LLM digestion sharing the same machine.
- **Correctness risk (not just performance):** DuckDB enforces **one read-write process OR many
  read-only processes — never both at once**. If the serving process opens the store while the
  nightly writer holds it read-write (or vice versa), connections are refused / error. This must be
  enforced structurally, not assumed.
- **Verdict:** **Requires modification** — do not put live OLAP queries in the request path
  (see R3/R4).

### Path D — transport / static tier

`serve.py` is FastAPI serving file contents from within the Python process. For "high throughput,"
ASGI + the GIL + per-request Python work cap throughput well below a static file server / object
store / CDN, even for the immutable dated artifacts. **Requires modification** for high load (R2).

---

## Comparison: current vs refactored vs refactored + recommended

| Serving concern | Current | Refactored (as proposed) | + Recommended mods |
|---|---|---|---|
| Night by date (`/{as_of}`) | Good — immutable, cacheable | Same | Same, fronted by CDN → ~0 origin |
| Index / `latest` | **O(N) parse-all, uncached → degrades linearly** | **Unchanged — still O(N)** | **O(1) materialized index + ETag** |
| Time-series charts | Not offered | New live DuckDB scans — concurrency-fragile | Pre-materialized static series → no query in path |
| Ad-hoc queries | n/a | Live DuckDB, no isolation | RO pool + memory caps + timeouts + snapshot replica |
| Writer/reader isolation | Safe (serve reads JSON) | **At risk** if DB-backed reads added | Snapshot/RO replica enforces one-RW-or-many-RO |
| Throughput ceiling | Python file IO | Python file IO + OLAP | Static/CDN tier; DB only for the long tail |
| Memory under load | O(N×filesize) per index request | Same + per-query OLAP buffers | Bounded (materialized + capped pool) |

Net: the refactor **improves the backend** (and so indirectly protects any future DB read by shrinking
the store ~500×), is **neutral** on the already-good dated-artifact path, **does not fix** the index/
latest bottleneck, and **adds** a new concurrency risk. Hence **Requires Modification**, not "Not
Suitable" — the foundation is right.

---

## Recommended improvements

Ordered by impact on frontend responsiveness under load.

- **R1 — Materialize the night index (biggest win, smallest change).** Stop deriving the index by
  scanning+parsing every file per request. Maintain an append-only `index.json` (or a tiny SQLite/
  DuckDB catalog) updated **only when a new night is recorded** — each entry already needs just
  `as_of`, `generated_at`, `snapshot_id`, `n_items`, all known at record time. Serve it O(1) with a
  strong `ETag`/`Last-Modified` (the index changes once/day → cache with revalidation, not
  `no-cache`). Make `/dashboards/latest` a pointer to the last entry (302 or a tiny cached doc), not a
  full re-scan. This removes the only path that degrades with history length.

- **R2 — Front immutable artifacts with a static/CDN tier.** Put `dashboards/<as_of>.json` on a static
  file server / object store / CDN; let the Python app handle only dynamic endpoints. The dated
  endpoint is already `immutable`; add `ETag`/gzip/brotli. This makes dashboard throughput effectively
  unbounded and takes the common read entirely off the app.

- **R3 — Serve time-series from a pre-materialized gold tier, not live OLAP.** At the end of the
  nightly job, precompute compact per-fund / per-factor series (small Parquet or JSON) — the same
  cadence that produces `dashboard.json`. The frontend then reads **static, cacheable** series with no
  query in the request path. This removes DuckDB-under-concurrency from the common charting case
  entirely and keeps latency flat regardless of store size.

- **R4 — If live/ad-hoc queries are truly needed, isolate and bound them.** (a) Open the store
  **read-only** from a **bounded connection pool**; (b) set per-query memory caps, `statement_timeout`,
  and result-row limits so one query can't starve others or OOM the box; (c) isolate the reader from
  the writer via a **DuckLake snapshot or a read-only file replica** taken after each nightly run, so
  the one-RW-or-many-RO rule is satisfied structurally; (d) accept that if concurrency ever becomes
  genuinely high, DuckDB stays the **batch/analytical** engine and a purpose-built serving store
  (ClickHouse/Pinot) — or simply more CDN'd static aggregates — is the scalable answer.

- **R5 — Hygiene for any list/collection endpoint.** Pagination + field projection, response-size
  caps, compression, and `ETag`s everywhere. Never return an unbounded list.

- **R6 — Make scalability testable.** Synthesize multi-year histories (thousands of nights) as a
  fixture and assert index/`latest`/time-series latency stays within a budget as N grows. This catches
  the O(N) regression in CI instead of in production, and pairs naturally with realistic-scale
  fixtures.

---

## Bottom line

For the **primary** frontend need — open a given night, or the latest one — the design is sound once
**R1** (materialized index) and **R2** (static/CDN tier) are in place; both are small. For **richer
time-series delivery under load**, do **R3** (pre-materialized static series) and treat live DuckDB
queries (**R4**) as a bounded, isolated escape hatch rather than the default path. With these, the
refactor supports large-scale, low-latency, high-throughput delivery. Without R1 in particular,
frontend responsiveness degrades linearly with every night recorded — regardless of how good the
write-path refactor is.
</content>
