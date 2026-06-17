# factor-scope — Data-Flow Architecture: live feeds, scale, and the durable target

This is a data-flow design companion to `CLAUDE.md`. It answers three questions the nightly-batch →
API → frontend future raises:

1. **What does a complete *live* run actually pull** (every CN + US feed) and **how many artifacts
   does it create per day?**
2. **Is the current data flow maintainable and scalable** for a job that runs every night for years
   and is then exposed as a read API?
3. **What is the optimal target architecture** — including whether DuckDB survives a year (and
   beyond) of nightly runs, and how fixtures should reflect the live run.

The short answer: the *reasoning* layer is well-designed (append-only, point-in-time, deterministic,
snapshot-fingerprinted, and the serving path already reads immutable JSON, not the DB). The
**ingest → store write path is not scalable as written** — it manufactures ~**500–600× redundant
data with quadratic growth** because it re-fetches *full histories* for the *entire* on-exchange
universe every night and the store's identity key includes a per-run `fetched_at`, so nothing
deduplicates across nights. DuckDB is *not* the bottleneck; the data flow is. Two targeted ingest
fixes plus a medallion/DuckLake storage split make the system last a decade-plus on a single
machine.

---

## Part 1 — Every feed a complete live run touches (CN + US)

A live run is `factor-scope ingest` (fills the durable store) then `run`/`build_dashboard` (reads it
→ `dashboard.json`). The market adapter (`factor_scope/markets/ashare.py`) composes the feeds below.
`config.source == "live"` (the default; `--offline`/`FACTOR_SCOPE_OFFLINE=1` flips to fixtures).

### China (A-share / funds & ETFs)

| # | Feed | Adapter | Live source | Granularity | What it returns per run |
|---|------|---------|-------------|-------------|--------------------------|
| 1 | **Fund universe** | `ingest/fund_universe.py` | AkShare `fund_name_em` + `fund_etf_spot_em` + `fund_exchange_rank_em` | bulk, all funds | **~20,000 rows** (one per fund, re-stamped `as_of`=tonight) |
| 2 | **ETF scale (AUM/shares)** | `ingest/etf_scale.py` | AkShare `fund_etf_spot_em` | bulk, one frame spanning both exchanges | ~1,500 rows (one per ETF) |
| 3 | **Fund holdings** | `ingest/fund_holdings.py` | AkShare `fund_portfolio_hold_em` | **per on-exchange ETF** | ~10–50 rows × **every on-exchange ETF** |
| 4 | **Trading activity** (turnover 换手率 + 成交额) | `ingest/trading_activity.py` | AkShare `fund_etf_hist_em` | **per on-exchange ETF, FULL history** | **full daily history** × every ETF |
| 5 | **Fundamentals (PE)** | `ingest/fundamentals.py` | AkShare `stock_zh_index_value_csindex` | **per mapped ETF's tracked index** | ~20 trailing PE rows × each mapped ETF |
| 6 | **Prices / NAV** | `ingest/prices.py` (+ `baostock.py`, `mootdx.py`) | AkShare `fund_etf_hist_em`, Baostock, Mootdx — triple-sourced + reconciled | per code, **last bar only** | 1 reconciled row × **book codes only (3)** |
| 7 | **End-demand revision** | `ingest/demand.py` | AkShare `macro_china_industrial_production_yoy` | bulk, one book-wide series | a handful of rows |

### United States

| # | Feed | Adapter | Live source | Granularity | Per run |
|---|------|---------|-------------|-------------|---------|
| 8 | **EDGAR 13F-HR / N-PORT-P** | `ingest/edgar.py` | EdgarTools `Company(cik).get_filings(...)` | per configured CIK | latest filing × `config.edgar_ciks` (**empty by default**) |
| 9 | **FRED macro dial** | `ingest/fred.py` | `fredapi` `get_series().iloc[-1]` | per series, **last obs** | **6 rows** (`DGS10, DFII10, T10YIE, DTWEXBGS, DEXCHUS, WALCL`) — needs `FRED_API_KEY` |

### Internal / discovery (not market-network)

| # | Feed | Adapter | Source | Per run |
|---|------|---------|--------|---------|
| 10 | **Positions** (the book) | `ingest/positions.py` | local `positions.csv` (never network) | 3 rows (fixture book) |
| 11 | **Prior calls** (self-scoring seed) | `ingest/calls.py` | accumulate from the digest nightly | grows by # leans/night |
| 12 | **Themes** | `ingest/themes.py` | the separate `discover` service (BERTopic + LLM) | not wired into nightly |
| 13 | **Text corpus** | `ingest/textstream.py` | HTTP `textstream_feed_url` | discovery-cadence only |

**The cost-driving asymmetry.** Prices (feed 6) are pulled for the **book's 3 codes** only, but
holdings/trading/fundamentals (feeds 3–5) loop over the **entire on-exchange ETF universe**
(`markets/ashare.py:83-87`), and feeds 4–5 re-pull the **full multi-year history** every single
night. That loop — universe-wide × full-history × nightly — is where all the volume comes from.

---

## Part 2 — Artifacts created per day, and why it grows quadratically

### Per-run *output* artifacts (small, healthy)

These are fine and need no change:

- `out/dashboard.json` — the morning artifact, **~20–40 KB**, overwritten nightly, byte-for-byte
  deterministic, fingerprinted by `snapshot_id`.
- `out/dashboards/<as_of>.json` — the immutable per-night archive (first write wins). **One ~20–40 KB
  file per night ≈ ~7–10 MB/year.** This is what `serve.py` exposes.
- `out/run.jsonl` — one ops-telemetry line per run (append-only).
- `out/graph.ladybug` — the holdings look-through graph (idempotent, temporal edges).

### Per-run *store* writes (the problem)

`fetched_at = fetched_at_for(as_of) = "{as_of}T22:00:00Z"` is derived from the run date in **live as
well as offline** (`markets/base.py:72`), so it changes every night. The original store identity was
`PRIMARY KEY (series, key, as_of, fetched_at, payload)` with `ON CONFLICT DO NOTHING`: because
`fetched_at` moved nightly, **re-fetching the same historical bar tomorrow produced a *different*
primary key** → it was inserted again, and the `ON CONFLICT` guard only deduped *within one run's
retries*, never across nights. `append` now keys on content instead (see Part 5.1b — **shipped**):
a reading is written only when its payload differs from the latest revision already held for that
`(series, key, as_of)`, so an unchanged re-fetch is a cross-night no-op. The figures below are the
*pre-fix* write volume that change removed.

Estimated rows written per night in year 1 (≈1,000 on-exchange ETFs; ~800 avg history bars each):

| Series | Rows / night | Genuinely new info / night |
|--------|-------------:|---------------------------:|
| `trading_activity` (full history × all ETFs) | ~800,000 | ~1,000 (1 new bar/ETF) |
| `fundamentals` (full PE history × all ETFs) | ~600,000 | ~1,000 |
| `fund_universe` (all funds, re-stamped) | ~20,000 | ~tens (membership deltas) |
| `fund_holdings` (full re-fetch, quarterly data) | ~30,000 | ~0 except at quarter-ends |
| `etf_scale` | ~1,500 | ~1,500 |
| `prices` / `fred` / `demand` / `positions` | ~15 | ~15 |
| **Total** | **≈1.45 million** | **≈2,500** |

**Write amplification ≈ 1,450,000 / 2,500 ≈ ~580×.** ~99.7% of every night's writes are
byte-identical to rows already in the store; only `fetched_at` differs.

### Why it is quadratic, not linear

Each ETF's history grows by one bar per trading day, and the whole history is re-appended nightly.
After `Y` trading nights the cumulative `trading_activity` row count is

```
Σ_{t=1..Y} (H₀ + t) × N_etf  ≈  N_etf · (Y·H₀ + Y²/2)
```

With `N_etf≈1,000`, `H₀≈800`:

| Horizon | `trading_activity` rows | + `fundamentals` | Store total (all series) |
|---------|------------------------:|-----------------:|-------------------------:|
| **1 year** (Y≈250) | ~231 M | ~181 M | **~425 M rows** |
| **2 years** (Y≈500) | ~525 M | ~410 M | **~1.0 B rows** |
| **3 years** (Y≈750) | ~880 M | ~690 M | **~1.7 B rows** |

The nightly *insert* itself grows every night (more rows re-appended), and `read_as_of`'s
`row_number() OVER (PARTITION BY key …)` window must scan an ever-larger, ~99.7%-duplicate partition.
This is the maintainability/scalability wall — reached in **months, not years**.

---

## Part 3 — Will DuckDB survive a year (and beyond)?

**DuckDB is the right engine and is *not* the limiting factor.** Published results show a single
DuckDB file aggregating 1B+ rows (~50 GB) on a 16 GB laptop, and 3–25× speedups over recent
versions. For this workload — local-first, single machine, columnar analytics, append-mostly — it is
an excellent fit and there is no reason to leave it.

The caveats are about *how it is driven*, all of which the current flow trips:

1. **Concurrency is strict: one read-write process OR many read-only processes — never both at
   once** (DuckDB docs, confirmed). A future API that opens the *same* file read-write while the
   nightly job writes will be refused. Today this is dodged only because `serve.py` reads JSON
   archives, not the DB. Any DB-backed endpoint must open **read-only**, and the nightly writer must
   be a **short-lived** RW process that commits and releases.
2. **WAL blow-up on concurrent access during ingest.** If anything reads/writes the file *while* a
   large ingest is in flight, the `.wal` can balloon to many× the DB size. A multi-million-row
   nightly insert is exactly the trigger.
3. **The quadratic data itself.** DuckDB *can* hold a billion rows, but holding a billion rows that
   are 99.7% duplicates — and inserting an ever-growing slab nightly — wastes the budget and slows
   both ingest and `read_as_of`.

**Verdict:** *With the Part-5 ingest fixes*, the true daily delta is ~2,500 rows → ~625 K rows/year
→ **single-digit millions after a decade** — which a single DuckDB file serves in sub-second time
essentially forever. **Without** the fixes, the single file becomes painful within the first year and
untenable by year two regardless of engine. Fix the flow first; the engine question then answers
itself.

---

## Part 4 — What modern, similar systems do (research)

- **Bitemporal / point-in-time stores are the canonical way to kill look-ahead bias.** Record each
  fact on two timelines — *valid time* (`as_of`/disclosure) and *system/transaction time*
  (`fetched_at`/revision) — and answer "as known on date D" queries. factor-scope already models
  this correctly; the bug is that it stores a *new revision every night even when nothing changed*. A
  true bitemporal log stores a revision **only when the value actually changes**.
- **Medallion architecture (bronze → silver → gold)** is the standard layering for batch pipelines
  that feed an API: bronze = raw landed payloads (replayable), silver = cleaned/deduped
  point-in-time facts, gold = consumer-ready artifacts. Quality gates live at the layer boundaries.
  factor-scope maps cleanly: bronze = raw provider responses, silver = the `readings` log, gold =
  `dashboard.json`.
- **Incremental ingest with watermarks** is universal: fetch only what is newer than the last stored
  high-water mark per series/key, never the full history each run.
- **DuckLake v1.0 (GA Apr 2026)** = lakehouse format with metadata in a SQL catalog
  (SQLite/DuckDB/Postgres) and data in Parquet. It adds snapshots + time-travel, ACID appends without
  small-file/metadata bloat, compaction, and — critically — clean **multi-process reads while a
  single writer commits**. For a single-machine local-first app, the SQLite/DuckDB catalog keeps it
  dependency-light; no Postgres required.
- **Serving layer:** immutable, content-addressed artifacts behind cache-forever URLs (exactly what
  the per-night `dashboards/<as_of>.json` already are) are the cheapest, most scalable read path;
  point a CDN at them. Heavier "time-series of a factor" queries go to a **read-only** DuckDB/DuckLake
  connection over the silver Parquet.

---

## Part 5 — The optimal target architecture

Keep what is good (append-only point-in-time semantics, determinism via the snapshot boundary,
immutable JSON serving). Fix the write path; layer storage for growth.

### 5.1 Two ingest fixes (highest leverage — do these first)

**(a) Incremental ingest (watermarks) — shipped.** Before each per-fund re-pull the universe loop
reads that `(series, key)`'s latest stored `as_of` and the adapter requests only newer observations
— `trading_activity` via AkShare's `fund_etf_hist_em` `start_date`, holdings via the disclosure year
derived from the watermark (the run stamp's year when nothing is stored — never a hard-coded
lookback). This turns *quadratic → linear*, cutting both network time and write volume by ~99.7%. The
CSI valuation feed already returns only a short trailing window, so `fundamentals` applies the
watermark as a write-filter and leans on content dedup below for the rest. The whole-universe
`fund_universe`/`etf_scale` snapshots stay full re-pulls — delisting detection needs the complete
membership each night.

**(b) Store only real revisions (content dedup) — shipped.** `append` skips a row when the most
recent stored revision for its `(series, key, as_of)` already has an identical `payload`, keeping a
new row **only when the payload genuinely changed** (a real restatement) — exactly the bitemporal
contract the docstring promises. This preserves "a later disclosure never rewrites an earlier read"
while ending the duplicate firehose. The identity is now `PRIMARY KEY (series, key, as_of,
fetched_at)` — `payload` left the key, and `fetched_at` records *when* a real change was seen — with
the insert gated on "payload differs from the latest revision for this `(series,key,as_of)`".

Together these make the silver store a minimal bitemporal log: ~2,500 new rows/night, linear growth,
trivially within DuckDB's envelope for a decade-plus.

### 5.2 Medallion storage split

- **Bronze (raw landing, optional but recommended).** A non-deterministic research/ingest job
  writes raw provider responses to Parquet partitioned by `source`/`as_of`. Enables
  replay and debugging; ages out after the silver layer is trusted. This is the *only* layer that
  touches the network.
- **Silver (the point-in-time `readings` log).** Stays DuckDB-backed and append-only. Recent window
  hot in the DuckDB file; cold data exported to **Hive-partitioned Parquet** (`series=…/year=…/`) via
  `DuckDBStore.tier_cold`, queried in place — every read unions hot + cold transparently. The
  deterministic reasoning pipeline reads only this layer (the snapshot boundary), keyed by
  `snapshot_id`.
- **Gold (the artifact).** `dashboard.json` + the immutable `dashboards/<as_of>.json` archive —
  unchanged. Already content-addressed and cache-friendly.

### 5.3 Adopt DuckLake when multi-process serving lands

Once an API reads the store live (not just JSON), migrate silver onto **DuckLake** (SQLite or DuckDB
catalog, Parquet data). It resolves the "one RW vs many RO" constraint directly: the nightly writer
commits a snapshot; serving processes read prior snapshots concurrently and never block the writer.
Time-travel maps onto per-night reproducibility; compaction handles small-file growth. Until then, a
single DuckDB file plus the 5.1 fixes is sufficient — adopt DuckLake as a *seam*, not a prerequisite.

### 5.4 Serving layer

- **Dashboard reads:** keep serving the immutable `dashboards/<as_of>.json` (cache-forever,
  CDN-able). Already correct.
- **Time-series reads** (a factor's history, a fund's NAV trail): add read-only endpoints backed by a
  **read-only** DuckDB/DuckLake connection over silver Parquet. Never the writer's handle. Cache
  aggressively; tighten the currently-open CORS before public exposure.
- **Writer discipline:** nightly ingest is a short-lived RW process — open, ingest incrementally,
  commit, close — so the read/write windows don't overlap.

---

## Part 6 — Make fixtures reflect the live run (the refactor)

**Today the offline path is a *different system* than live.** `config.source == "fixtures"` takes a
separate branch (`markets/ashare.py:60-79`) that loads small pre-baked CSVs (1 fund, ~1,200 rows) and
**bypasses** the universe-scale loop, the watermark/incremental logic, the multi-source
reconciliation + retry + circuit breaker, and delisting detection — all the live-only code is
`# pragma: no cover`. The most expensive, most bug-prone behavior is never exercised by the suite.

**Target: the only live/offline difference is the transport at the very edge.** Adopt a recorded-
snapshot ("cassette") fixture model:

1. Fixtures become **recorded raw provider responses** for a **realistic-shape** universe — e.g.
   ~30–50 on-exchange ETFs, a few hundred funds, multi-quarter holdings, ~800-bar daily histories —
   committed under `data/fixtures/`. Big enough to exercise the loop and the dedup, small enough to
   stay deterministic and fast.
2. The `fake` provider **replays** those recordings instead of hitting the network; the **same**
   ingest code (universe loop, watermark, reconciliation, dedup, delisting) runs in both modes.
3. Determinism is preserved: recorded responses + the deterministic `fetched_at_for(as_of)` keep
   `dashboard.json` byte-for-byte, and the snapshot boundary still freezes the reasoning input.

This is consistent with the snapshot boundary: ingest is the non-deterministic edge;
everything downstream is deterministic over a frozen snapshot. **Fixtures become a committed frozen
snapshot at realistic scale**, so `make test`/`make system` actually validates the incremental-ingest
and dedup paths that production depends on — and the artifact-volume math above gets a regression
test instead of living only in this document.

---

## Part 7 — Migration plan (standalone)

This refactor stands on its own. Ordered by leverage; each is one session / one PR / `make check` green.

1. **Content-dedup append** (§5.1b). Smallest change, biggest win; kills write amplification
   immediately. Unit-test that re-appending an unchanged payload is a no-op and a changed payload
   adds exactly one revision.
2. **Incremental watermark ingest** (§5.1a) for `trading_activity`, `fundamentals`, holdings,
   universe. Turns quadratic → linear.
3. **Cassette fixtures + unified ingest path** (§6). Removes the offline/online code fork; gives the
   expensive live paths real coverage.
4. **Cold-tier partitioned Parquet** (§5.2) via `DuckDBStore.tier_cold` with Hive layout + a recent-window
   policy.
5. **Read-only serving connection over silver** (§5.4) for time-series endpoints; keep JSON serving
   for dashboards.
6. **DuckLake migration** (§5.3) when multi-process live reads are needed.

Steps 1–3 are the ones that decide whether the system survives a year of nightly runs; 4–6 are the
durability/scale headroom once the write path is sound. Frontend-delivery scalability under load is
evaluated separately in `docs/SERVING_EVALUATION.md`.

---

## Appendix — estimate assumptions

- On-exchange A-share ETFs ≈ 1,000–1,100 (2026); all funds via `fund_name_em` ≈ ~20,000.
- Avg per-ETF daily history `H₀` ≈ 800 bars (≈3.2 listed years); PE history similar order.
- ~250 trading nights/year; the nightly job runs after close.
- Row counts are *rows*, not bytes; DuckDB compresses repeated VARCHAR payloads well, so on-disk size
  is far below `rows × payload-bytes`, but row count drives insert cost and `read_as_of` scan cost.
- `edgar_ciks` empty by default and theme/textstream feeds run on a separate discovery cadence, so
  US-holdings and theme volume are configuration-dependent, not part of the nightly baseline.
</content>
</invoke>
