# factor-scope — Durability over a year+ and for any dataset size

A companion to `docs/DATA_FLOW.md`. That note fixes *test fidelity* (offline must run the live
codepath) and the *obvious* per-run `O(history)` costs. This note asks the harder question the
request poses: **after a year of nightly execution against the full universe, is that still the
right design — and what makes the system durable as the dataset grows without bound?**

The short answer: the prior plan is **necessary but not sufficient**. It removes the per-run rebuild
costs that hurt on day one, but three deeper time-bombs only detonate once *history accumulates*, and
none of them are addressed by record/replay or an incremental snapshot id alone. This note proves
where they bite with a capacity model, then specifies the durability layer that sits beneath the
prior plan.

---

## 0. Direct answer to "will it still be best after a year?"

No — not on its own. Keep everything in `DATA_FLOW.md`, and add a durability layer, because three
costs in the current engine scale with **accumulated history `H`**, not just with universe size, so
they are invisible in week one and dominant by month six:

1. **The nightly theme→fund mapping is the real CPU cliff** — and it has a structural redundancy that
   makes it grow super-linearly. `infer_links` (`emerging/mapping.py:143`) is `O(themes × universe)`,
   and *inside* that loop `return_correlation` rebuilds each theme's reference index **once per
   candidate fund** (`_index_returns`, `mapping.py:81`), each rebuild reading **every constituent's
   full NAV history** via `store.history()` (unbounded; the `as_of` filter is in Python,
   `window.py:19`). It is recomputed from scratch *every night* and re-appended to the store.
2. **The reasoning working set is unbounded.** Every factor read pulls a key's *entire* history
   (`_series_asc`/`_dated_navs`), even though no factor needs more than a bounded lookback (200-day
   gate, `CORR_WINDOW=60`, `PE_HISTORY_MIN=12`). The work a single night does therefore grows forever
   even though the *information* a night uses is bounded.
3. **There is no lifecycle story** — no retention/archival, no bounded reprocessing/backfill, no
   schema-evolution path for a contract that *will* change over a year, and no way to test at scale
   without committing impossibly large fixtures.

The prior plan's incremental `snapshot_id` and `index.jsonl` are still correct and still needed. They
are just the *first* two of a longer list.

---

## 1. A capacity model (why and when each cost bites)

Let `N` = funds in the universe (`U06` target ≈ 10⁴, on-exchange ETFs ≈ 10³), `D` = 250 trading
days/yr, `T` = themes (≈ 12), `C` = constituents/theme (≈ 10), `H` = accumulated trading-day history
(grows: ≈250 by year 1, ≈750 by year 3). Order-of-magnitude, not exact.

### 1a. Store growth — and how much of it is waste

| Series | Rows/night | New information? |
|---|---|---|
| `prices` (NAV/fund/day) | ≈ N (10⁴) | **Yes** — genuinely new daily |
| `trading_activity` (ETF turnover/day) | ≈ 10³ | **Yes** — new daily |
| `fundamentals` (PE) | ≈ 10³ | partly (slow-moving) |
| `fund_universe` | ≈ N (10⁴) | **No** — re-appended unchanged nightly |
| `etf_scale` | ≈ 10³ | **No** — re-appended unchanged nightly |
| `theme_map` (derived) | ≈ T·candidates (≈ 600) | **No** — recomputed + re-appended nightly |
| `holdings` (quarterly, amortised) | ≈ 500 | **Yes**, on disclosure days |

≈ **24k rows/night → ~6M rows/year**, of which **~half is reference data re-appended unchanged**
every night. The cause is mechanical: `fund_universe`/`etf_scale` are stamped with *tonight's*
`as_of` (`markets/ashare.py:54`), so each night is a new `(series, key, as_of, fetched_at, payload)`
tuple even when the fund didn't change — the store's idempotent dedup (`store.py:92`) never fires
because `as_of` moved. Point-in-time semantics do **not** require this: a disclosure that didn't
change is not a new disclosure.

### 1b. `snapshot_id` — `O(total store)` per night, forever

`store.snapshot_id` selects, orders, serialises, and SHA-256s *every* reading with `as_of ≤ D`
(`store.py:146`). At ~6M rows after year 1, ~18M after year 3, this is a full re-hash of the entire
store **every night**, growing linearly without bound — seconds today, minutes by year 2-3, and the
cost is paid even when nothing relevant changed.

### 1c. The mapping — the dominant cost, and it's worse than linear

Per night `infer_links` does, per theme, per fund: an overlap graph query **plus** a
`return_correlation` that internally calls `_index_returns(constituents)` — rebuilt **for every
fund**. So the index work alone is `O(T · N · C · H)`:

```
year 1:  T·N·C·H ≈ 12 · 10⁴ · 10 · 250  ≈ 3 × 10⁸  reading-ops / night
year 3:  T·N·C·H ≈ 12 · 10⁴ · 10 · 750  ≈ 9 × 10⁸  reading-ops / night   (grows with H, unbounded)
```

Two independent bugs stack here: the index is recomputed `N` times when it should be computed **once
per theme**, and each read pulls **full** history when it needs only `CORR_WINDOW=60` days. Fixing
both turns the cost into `O(T·N·CORR_WINDOW)` ≈ `12 · 10⁴ · 60 ≈ 7 × 10⁶`, **flat forever** — a ~40×
cut in year 1 and an *unbounded* cut over time. This single item is the difference between a nightly
run that stays minutes and one that grows toward hours.

### 1d. Serving — `O(nights)` per request

`history.read_index` fully parses every `<as_of>.json` to emit four fields (`history.py:51`); at
365+ immutable nights this is multi-MB of parse per `/dashboards` call. (Fixed by the prior note's
persisted `index.jsonl`.)

**Takeaway:** storage growth is *halvable* (1a); `snapshot_id` and serving are *linear and fixable*
(1b, 1d); the mapping is *the cliff* (1c) — and the master lever under all of them is **bounding the
working set**.

---

## 2. The durable design — seven principles

### P1 — Bound the reasoning working set; let the archive grow unbounded. *(the master lever)*

A night never needs more than a bounded lookback: the 200-day gate, `CORR_WINDOW=60`,
`PE_HISTORY_MIN=12`, the scorecard window (`60d`). Define a single `LOOKBACK_HORIZON` (the max any
factor needs, plus a safety margin — say ~400 trading days) and make the point-in-time reads
**time-windowed**: `read_as_of(series, as_of)` and the per-key history helpers take a
`since`/horizon and return only `[as_of − HORIZON, as_of]`. Push the bound into SQL (`WHERE as_of
BETWEEN ? AND ?`) so DuckDB scans a bounded slice, not the whole series.

This converts *every nightly read* from `O(H)` to `O(HORIZON)` — constant in calendar time. The
archive (`as_of < as_of − HORIZON`) still exists for audit and backfill but is never touched by a
normal night. This is the one change that makes "any growing dataset size" true: the **working set is
bounded even as the system of record is unbounded.**

### P2 — Append on *change*, not on *observation*, for slow-moving series.

For reference series (`fund_universe`, `etf_scale`, inception, static `fundamentals`), append a new
dated row only when the payload differs from the last known value for that key (slowly-changing-
dimension semantics). Daily series (`prices`, `trading_activity`) keep appending every day. This
removes the ~half of row growth that is re-appended-unchanged (1a) while *strengthening* point-in-
time correctness: a genuine new disclosure is still a new row; a no-op re-read is a true no-op. It
depends on decoupling `fetched_at` from `as_of` (the prior note) so the dedup key actually collides
on an unchanged re-read.

### P3 — Incrementalise every full-rebuild.

- **`snapshot_id`** → a Merkle/rolling structure over **per-`(series, as_of)` digests**: tonight's id
  recombines the cached series-digests, recomputing only the partition that changed. `O(changed)`,
  same value as today (1b).
- **The graph** → add only tonight's *new* holdings disclosures; the temporal window logic already
  closes prior windows (`graph/store.py:185`), and MERGE makes a re-run a no-op.
- **The mapping (the cliff, 1c)** → it already persists as append-only `theme_map`; make that the
  *cache* it was meant to be. Recompute a link only when an input changed (the fund's holdings
  disclosure, its recent NAVs, or the theme's constituents), reuse the dated row otherwise, and
  **compute each theme's index once** (hoist `_index_returns` out of the per-fund loop) over a
  **bounded** window (P1). Together: `O(T·N·H)` and rising → `O(Δfunds · CORR_WINDOW)`, flat.

### P4 — A tiered lakehouse, with DuckDB as the query engine over it.

Three tiers, one query interface:
- **Hot** — the recent window in the live DuckDB file (what nightly reads, bounded by P1).
- **Warm** — older readings rolled to **Parquet partitioned by `series`/year-month** (the
  `export_parquet` seam already exists, `store.py:159`); DuckDB queries Parquet *in place*
  (`read_parquet`), so backfill/audit can still scan everything without inflating the hot file.
- **Cold** — archived partitions (object storage), attached only on demand.

The `PointInTimeStore` Protocol (`store.py:40`) already abstracts the backend, so this is an
implementation behind a stable seam, not a contract change. This is the durable answer to *all*
dataset sizes: the archive is columnar, partitioned, and cheap; the working set stays small.

### P5 — Reprocessing/backfill is first-class and bounded.

Over a year you *will* change a factor, fix a bug, or add a state, and need to replay history to
re-derive the scorecard and re-emit artifacts. The append-only **bronze `Readings` log is the system
of record; every other layer (graph, `theme_map`, states, the artifact) is a deterministic,
rebuildable projection of it.** Provide `reprocess(date_range)` that re-derives the projections and
re-emits artifacts from the frozen bronze layer. Because nightly reads are bounded (P1) and
projections are incremental (P3), replaying `R` nights is `O(R · HORIZON)`, not `O(R · total)` — the
difference between a tractable backfill and one that's quadratic in the year. (Note: this requires
relaxing history's strict first-write-wins for *explicit, audited* reprocessing — a versioned re-emit,
not a silent rewrite — so the immutability invariant for *normal* nights is preserved.)

### P6 — Version the contract and upcast on read.

A year of nightly artifacts will span multiple contract versions as factors and fields are added.
`schema_version` already exists (`contract/__init__.py:180`) but nothing reads it. Make the serving
layer **multi-version tolerant**: keep old nights byte-for-byte on disk (immutability), register
`upcast_v{n}→v{n+1}` transforms, and apply them at the API boundary so a frontend always sees the
current shape. Store payloads can carry their own per-series version for the same reason. This keeps
the whole history queryable through one evolving frontend without ever rewriting an old night.

### P7 — Test at any scale without giant fixtures.

Recorded cassettes (the prior note) give *fidelity* at small scale, but you cannot commit a 10⁷-row
fixture. Add a deterministic **synthetic generator** parameterised by `(N_funds, D_days, T_themes,
seed)` that fabricates a store at any scale, plus a **perf-budget** check in CI: a night at 10×
universe must stay under a wall-clock/row budget. This is the regression guard that keeps P1–P5
honest as the targets move — and it closes the loop with the fixtures concern: cassettes prove
*correctness against real shapes*, the generator proves *scaling against real volumes*.

---

## 3. Component change-list

| Component | Today | Durable target | Principle |
|---|---|---|---|
| `read_as_of` / `_series_asc` / `_dated_navs` | full per-key history, filtered in Python | bounded-window read pushed into SQL | P1 |
| `fund_universe` / `etf_scale` ingest | re-appended nightly | append-on-change (SCD-2) | P2 |
| `snapshot_id` | hash whole store/night | Merkle over per-`(series,as_of)` digests | P3 |
| `build_graph_from_store` | rebuild from full history | add tonight's new disclosures only | P3 |
| `_materialise_mapping` / `infer_links` | recompute nightly; index per-fund; full history | cache delta; index once per theme; bounded window | P3 |
| store backend | single DuckDB file | hot DuckDB + warm Parquet + cold archive | P4 |
| reprocessing | re-run nights one by one | bounded `reprocess(date_range)` | P5 |
| serving index | parse every artifact | persisted `index.jsonl` (prior note) | — |
| frontend time series | open N artifacts | derived columnar history view + static/CDN export | P4/P6 |
| contract over time | `schema_version` unused | upcast-on-read, multi-version serving | P6 |
| scale testing | hand-authored small fixtures | deterministic generator + CI perf budget | P7 |

---

## 4. Serving a frontend over a year of nights

- **Index**: persisted `index.jsonl`, `ETag = snapshot_id` (prior note).
- **Cross-night series**: a frontend charting a fund's lean/score over time should not open 365
  files. Materialise a derived, columnar **history view** (per-item time series projected from the
  artifacts, or queried from the `calls`/states series) so a chart is one bounded query.
- **Static export**: because each night is an immutable JSON, a small publish step pushes the history
  to object storage / a CDN and the frontend reads static JSON directly — the API becomes a thin
  index, or disappears. This is the cleanest read-scaling story and fits "one immutable artifact per
  night" exactly. Defer until a real frontend exists (`U16`).
- **Retention**: keep every artifact (small, immutable, the audit trail) but paginate and lazy-load
  the index; never assume the client wants all of history at once.

---

## 5. Capacity budget & graduation triggers

Set explicit, checkable SLOs so a regression is caught by the perf budget (P7), not by a slow night:

- **Run time**: a full-universe night stays `< ~10 min` on the Mac mini (after P1+P3).
- **Working-set rows scanned/night**: bounded by `HORIZON`, i.e. roughly flat in calendar time.
- **Storage**: ~3M rows/yr after P2 (down from ~6M), partitioned to Parquet beyond `HORIZON`.
- **API p95**: `< ~100 ms` for index + a single night (after `index.jsonl` + static export).

**Graduation to `U17` (ArcticDB / DSR / PBO / CPCV)** triggers when warm-Parquet query latency or the
hot-store size crosses the budget despite P1–P4 — i.e. when the single-box lakehouse is genuinely
outgrown, not before (YAGNI). The capacity model above says that, with P1+P3, the *nightly* cost is
flat in calendar time, so the trigger is driven by *universe* growth (a new market, `U02`), not by
the passage of time.

---

## 6. Sequencing (what makes it durable, in dependency order)

1. **Bound the working set (P1)** + **append-on-change (P2)** — cap growth at the source *before*
   `U06` floods the store; everything else gets cheaper once reads are bounded. *(touches
   `store.py`, `factors/window.py`, `emerging/mapping.py`, the reference adapters)*
2. **Incrementalise the mapping (P3, the cliff)** — index-once + delta cache + bounded window; the
   biggest single CPU win. *(`emerging/mapping.py`, `pipeline.py:503`)*
3. **Incremental `snapshot_id` + graph (P3)** — flat per-night fingerprint and graph build.
   *(`store.py`, `graph/store.py`)*
4. **Lakehouse tiering + bounded `reprocess` (P4, P5)** — the archive/backfill story. *(`store.py`,
   a new reprocess entry point)*
5. **Schema upcasting + frontend history view / static export (P6)** — durable serving as the
   contract evolves. *(`contract/`, `serve.py`, `history.py`)*
6. **Synthetic scale generator + CI perf budget (P7)** — the guard that holds 1–5 in place; land its
   first version alongside step 1 so each later step is measured.

Steps 1–2 are the ones that change the answer to "still best after a year?" from *no* to *yes*; 3–6
keep it that way as the dataset and the contract grow.

---

## 7. Bottom line

`DATA_FLOW.md` makes the engine **trustworthy** (offline runs the live codepath) and removes the
day-one rebuild costs. This note makes it **durable**: by *bounding the working set* (P1) the nightly
cost becomes flat in calendar time, by *appending only changes* (P2) storage growth halves, by
*incrementalising the mapping* (P3) the dominant CPU cliff is removed and made history-independent,
and by adding a *lakehouse + bounded reprocessing + schema upcasting + a scale harness* (P4–P7) the
system stays correct and queryable for any dataset size and any number of nights — on the same
single, local box, with every hard rule in `CLAUDE.md` intact.
</content>
</invoke>
