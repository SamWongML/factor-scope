# Multi-Source Market-Data Ingestion — Professional Best Practices, Tailored to factor-scope

*Deep-research synthesis · 5-angle fan-out · 2026-06-06*

> **Method & confidence.** Findings come from a 5-agent parallel web search (failover/resilience,
> reconciliation, tolerance bands, error-handling/observability, point-in-time/lineage + CN-source
> fragility). `WebFetch` was HTTP-403 blocked environment-wide, so claims rest on WebSearch result
> summaries that directly quoted primary sources (SEC, CSSF law-firm notes, AWS Builders' Library,
> Google "Tail at Scale", Martin Fowler, Monte Carlo, ArcticDB docs, library GitHub issues). I label
> confidence **[H]/[M]/[L]** and flag single-source claims. This is a best-practices brief, not legal
> or compliance advice.

---

## TL;DR — verdict on factor-scope's current design

**What you already got right (keep it):**
- **Bitemporal stamping** (`as_of` = valid time, `fetched_at` = transaction time) is exactly the
  canonical model (Snodgrass/SQL:2011) and matches the leading quant store, Man Group's ArcticDB,
  which is bitemporal with an `as_of` read parameter. **[H]**
- **Append-only / never overwrite a prior as-of read** is the correct restatement-handling pattern
  (event-sourcing / ledger model): corrections append a new row, they don't mutate history. **[H]**
- **Multi-sourcing CN prices** is well-justified — AkShare's scraper breaks in the wild (documented
  outages + IP blocks), Baostock is "free but incomplete", Mootdx/pytdx depend on server health.
  No single CN community source is reliable enough to stand alone. **[H]**
- **Eager parallel fetch + cross-validate** (fetching both sources every night) is the *right* choice
  for a latency-insensitive **nightly batch** — batch jobs can "afford heavier validation." **[H]**
- **Relative (%) tolerance** rather than an absolute price gap is the correct axis. **[H]**
- **Testing the fall-back path** (your `test_..._when_akshare_is_down`) directly answers AWS's main
  warning that fallback paths are dangerous *because they're rarely exercised*. **[H]**

**The three things to change (detail + citations below):**
1. **Don't hard-fail the whole run on one divergent ETF.** Industry standard for batch pipelines is
   **quarantine/flag-and-continue**, with a batch-level circuit breaker (fail the run only if the
   *reject rate* is high). Raising `IngestError` on a single >1% divergence can kill the nightly run
   over one bad tick — the opposite of the anti-fragility goal. **[H]**
2. **Make the 1% tolerance asset-class-aware and tighter.** Regulators use relative, asset-specific
   NAV-error materiality bands: **equity ~1.0%, fixed-income/mixed 0.5%, money-market 0.2%** (CSSF
   24/856); SEC's de-facto is **0.5% / $0.01**. A flat 1% is the *loose* end even for equity ETFs and
   far too loose for a bond/MMF ETF. **[H]**
3. **Fix `except Exception: return []` and add source-tagging + degradation alerting.** Swallowing all
   exceptions into an empty list conflates "no data" with "fetch failed," and silently failing over to
   a secondary source with no record is the **#1 latent risk** here — silent degradation that can go
   unnoticed for weeks. **[H]**

**Single biggest risk today:** *silent failover.* If AkShare quietly breaks, the broad `except` drops
its read to `[]`, `select_corroborated` substitutes Baostock, and nothing records that the primary is
down or that cross-validation stopped happening. You could run for a month on an un-cross-checked
single source and never know. Fixing this (provenance + a degradation signal) is higher value than any
algorithmic refinement.

---

## Area 1 — Source failover & resilience

### What the pros do
- **A/B feed redundancy + arbitration.** Professional market-data systems run two independent feeds
  and arbitrate by sequence number, picking the first-arriving correct packet; failover targets are
  sub-30s for real-time. That's a *real-time* concern — not yours — but the **active/active, fetch-
  both-and-reconcile** shape is what you want for batch. **[H]** (exegy.com, ice.com consolidated feed)
- **Circuit breaker** (Fowler): a 3-state machine — CLOSED → OPEN (trip on failure threshold, return
  fallback) → HALF_OPEN (probe) — so a failing dependency gets time to recover. **[H]**
  (martinfowler.com/bliki/CircuitBreaker.html, resilience4j)
- **Retry with exponential backoff *plus jitter*.** Backoff without jitter causes a synchronized
  "thundering herd"; AWS's measured recommendation is **full jitter** `sleep = random(0, min(cap,
  base·2^n))`, which minimizes total work. **[H]** (aws.amazon.com/blogs/architecture/exponential-
  backoff-and-jitter)
- **Retry budgets / token buckets.** AWS argues a token-bucket retry quota beats a classic circuit
  breaker, which "introduces modal behavior … difficult to test." **[H]** (brooker.co.za, AWS
  Builders' Library: timeouts-retries-and-backoff-with-jitter). *Contradiction with the Fowler/
  resilience4j camp — see below.*
- **Hedged requests** (Google, "The Tail at Scale"): send a duplicate to a second backend only after
  the first passes ~p95 latency — caps extra load ~2-5% while cutting the tail dramatically (a BigTable
  example cut p99.9 from 1,800ms → 74ms with ~2% extra requests). Hedging is for **idempotent/read-only**
  calls (gRPC restricts it likewise). **[H]** (barroso.org/publications/TheTailAtScale.pdf, grpc.io)
- **AWS caution on fallback:** application-level fallback paths are a liability *because they're rarely
  exercised, can fail under the same correlated conditions, and mask problems.* **[M]**
  (aws.amazon.com/builders-library/avoiding-fallback-in-distributed-systems)

### Reconciled contradictions
- **Circuit breaker vs token bucket:** both exist to stop hammering a sick dependency. For your scale
  (a handful of ETFs, once a night) neither needs heavy machinery — a bounded **retry with backoff +
  jitter and a per-source "is it up?" flag** captures 90% of the value.
- **"Avoid fallback" vs "secondary feed is essential":** resolves by context. Hardware A/B hot-standby
  is well-exercised and deterministic; a *software* fallback branch is the rarely-exercised kind AWS
  warns about. The mitigations are exactly: **test it** (you do) and **monitor when it fires** (you
  don't yet).

### Recommendations for factor-scope
- **Keep** the eager fetch-both-and-reconcile design — correct for a nightly batch. **[H]**
- **Add** bounded **retry with exponential backoff + full jitter** and a **per-source timeout** inside
  each adapter's live read, so a transient blip or a hung socket doesn't waste the run or hang it. (P1)
- **Skip** formal circuit breakers / hedging — over-engineering at this scale. Track a simple per-source
  health outcome in the run log instead. (P2)
- When Mootdx lands (#21), you'll have **3 sources** → you can move from "primary + corroborator" to a
  **2-of-3 median/vote**, which is strictly more robust (see Area 2).

---

## Area 2 — Cross-validation & reconciliation

### Golden source vs consensus/median
- A **"golden source"** is one designated authoritative feed; the validated "gold copy" is produced by
  rule-based checks (stale-price, day-over-day tolerance, cross-vendor consistency), not blind trust.
  **[H]** (greshamtech.com, thegoldensource.com)
- **Consensus/composite** pricing collects N contributions and takes a representative value — commonly
  **trim the high/low, then mean, or use the median** — and is used where no single source is
  authoritative (Bloomberg BVAL, ICE MPV). **[H]** (clearconsensus.com, ice.com, bloomberg.com)
- **Pick which:** designate a **golden/priority source** when one feed is clearly superior; use
  **median/consensus** when ≥3 comparable sources exist. factor-scope today has a clear primary
  (AkShare) → golden-source-with-priority is the right model *for two sources*; with three, prefer
  **median**. **[M, synthesis]**

### Tolerance bands (the core of your `>1%` question)
- NAV-error materiality is **relative and asset-class-specific**: **[H]**

  | Regime / context | Materiality threshold |
  |---|---|
  | US mutual funds (SEC staff, de-facto) | **0.5% of NAV** or $0.01/share |
  | CSSF 24/856 — money-market | **0.2%** |
  | CSSF 24/856 — fixed-income / mixed | **0.5%** |
  | CSSF 24/856 — equity | **1.0%** |
  | CSSF — professional/well-informed | up to 5% (documented) |
  | IFIC (Canada) | 0.5% of NAV (+ $50/account floor) |
  | ISDA/OTC derivatives MtM **dispute** | 10% at netting-set level *(not a NAV threshold)* |

  Sources: sec.gov s7-03-13, ey.com (CSSF 24/856), maples.com, investmentexecutive.com, isda.org.
  Caveats: SEC declined to *codify* 0.5% (it's staff-acknowledged convention); verify the exact CSSF
  per-sub-class mapping against the primary circular before treating as authoritative.
- **Tolerances should be configured granularly** — per asset type / instrument / currency — "as
  granular as possible," and stated in **basis points** to disambiguate absolute vs relative. **[M-H]**
  (fundrecs.com, limina.com, stonex.com)

### Why two sources legitimately differ (don't treat these as errors)
- **Adjusted vs unadjusted close** is the dominant cause of spurious divergence: adjusted close applies
  split/dividend multipliers retroactively, so comparing an adjusted series to an unadjusted one
  produces large false breaks (a split looks like a price crash). **[H]** (riazarbi.github.io, eodhd.com)
- Even two *correctly* adjusted series differ by algorithm, rounding (2 vs 4 dp), and forward- vs
  backward-adjustment anchoring. **[H]**
- **NAV vs official close vs last-trade vs fair-value**, and **snapshot-timing/staleness** (T vs T-1),
  are structural legitimate differences. **[H]** (etf.com, natixis.com)
- **Point-in-time:** comparing values at different `as_of` coordinates is meaningless; bitemporality is
  what makes the comparison valid. **[H]** (vbase.com, juxt.pro, xtdb)

### What to DO on disagreement — quarantine, don't hard-fail
- The industry-standard batch pattern is **quarantine / dead-letter the bad record** (with
  `rejection_reason`, source, severity, timestamp) and **let the rest of the batch proceed** — *not*
  silently drop, *not* reflexively kill the run. **[H]** (medium/towards-data-engineering "Fail Fast or
  Quarantine", francotesei dead-letter, victor-antoniassi silver-layer gist)
- Hard-failing the whole run on one bad value is called a **naive anti-pattern**; the mature pattern is
  a **"circuit breaker on data"**: quarantine individual records, but if the **batch reject rate exceeds
  a threshold (commonly ~10%)**, fail the whole run as evidence of a systemic problem. **[H]**
- Enforce **reconciliation accounting** so nothing is silently lost: `inputs = outputs + rejects`. **[H]**
- Tools encode **severity tiers** (dbt `warn` vs `error`, Great Expectations) so only critical checks
  block. **[H]** (datadoghq.com, metaplane.dev)
- **Fail-fast is still right** for *structural* violations (schema, missing keys) where continuing
  corrupts downstream. So severity, not a blanket rule. **[H]**

### Recommendations for factor-scope
- **Change `select_corroborated` from raise-on-divergence to flag-and-continue.** On a >tolerance
  same-day disagreement: **keep the primary value, mark the Reading suspect** (a quality flag /
  quarantine note), record both values + the source, and **continue the run**. Surface it in the
  dashboard/run log rather than aborting. A single divergent ETF must not kill the nightly batch. (P0)
- **Add a batch-level circuit breaker:** if, say, **>10-20% of priced funds diverge**, *then* fail the
  run — that signals a systemic problem (e.g., one source switched to adjusted prices). (P1)
- **Make tolerance asset-class-aware:** default **0.5%** (the SEC/CSSF baseline), widen to **1.0%** only
  for equity-type ETFs, tighten to **0.2%** for money-market funds. Express in **bps**, configurable
  per fund. Your current flat 1% is the loosest defensible band. (P1)
- **Pre-empt the #1 false positive:** ensure both sources are on the **same adjustment basis**
  (raw close vs adjusted) before comparing — most "divergences" will be split/dividend artifacts, not
  bad data. Document which basis AkShare's `fund_etf_hist_em` and Baostock's `adjustflag` return. (P0)
- **You already gate the cross-check on matching `as_of`** — correct and important; keep it. **[H]**
- With Mootdx (3 sources), switch to **median-of-3 with MAD-based outlier flagging** (modified z-score,
  |z|>3.5, is the robust standard; plain z-score is fragile for prices). **[H]** (hausetutorials, MAD)

---

## Area 3 — Error-handling, provenance & observability

### The `except Exception: return []` critique
- **Bare/broad except is a recognized anti-pattern**: a bare `except:` even traps `KeyboardInterrupt`/
  `SystemExit`; `except Exception` masks unexpected bugs and, with a swallow, produces **silent
  failure**. **[H]** (realpython, Google Python Style Guide: "minimize code in try … the try/except
  hides a real error"; Ruff BLE001 "blind-except")
- **Conflating "no data" with "fetch failed" is a correctness bug.** An empty list is a *valid success*
  (the source genuinely had zero rows); a fetch failure is an *error state*. Collapsing both to `[]`
  means downstream can't tell "Baostock had no bar today" from "Baostock is broken." **[H]**
  (apollographql nullability, mcculloughwebservices)
- **A broad catch is acceptable only at a top-level boundary that logs with context and/or re-raises** —
  never to silently degrade. Ruff explicitly doesn't flag `except Exception` when it logs with
  `exc_info` or re-raises. **[H]**

### Silent degradation is the dominant operational risk
- Systems "rot quietly" — a failed-over/skipped source can go unnoticed indefinitely; the cure is
  **fail loudly**: log, emit a **metric**, and **alert** on fallback/degradation. Logging alone is
  insufficient — "an alert generated is not an alert seen." **[H]** (frugaltesting, sre.google workbook)
- **Data-observability (Monte Carlo's 5 pillars): Freshness, Volume, Schema, Distribution, Lineage.**
  A **Volume** monitor (row-count drop to zero) is precisely what catches a silently-empty load; a
  **Freshness** monitor catches a stale source. **[H]** (montecarlodata.com 5 pillars)

### Provenance / lineage — the missing field
- **Recording the source of each value is established best practice** so errors trace to origin and a
  bad source can be rolled back; provenance metadata should be a per-record schema (origin, timestamps,
  transformations). **[H]** (AWS Well-Architected AG.DLM.8, secoda, snowflake lineage)
- factor-scope stamps `series`/`key`/`as_of`/`fetched_at` but **does not record which source** a price
  came from. After a fall-back you cannot tell, weeks later, whether a given NAV was AkShare or
  Baostock. This is the concrete lineage gap. **[H, applied to your schema]**

### Idempotency for the append-only store
- Batch reprocessing must be **idempotent** (at-least-once + idempotent consumer = effective
  exactly-once): a deterministic dedup key + upsert/overwrite-partition, else a re-run inflates
  duplicate rows. For an append-only store, dedup on `(series, key, as_of, fetched_at, source)`. **[H]**
  (airbyte idempotency, conduktor exactly-once)

### Recommendations for factor-scope
- **Replace `except Exception: return []`** with: **catch specific exceptions** (network/timeout/parse),
  **log with context** (`logging.exception`, including code + source), **emit a degradation signal**
  into the run log, **return a result that distinguishes empty-vs-failed**, and let truly unexpected
  errors propagate. Keep the broad catch *only* at the per-source boundary, and only because it now
  logs+records rather than silently swallows. (P0)
- **Add a `source` field to every price Reading** (`"akshare"` / `"baostock"` / `"mootdx"`) — provenance
  so a bad source can be traced and rolled back, and so you can see *post-hoc* when cross-validation
  actually happened vs fell back. (P0)
- **Emit a per-run ingestion summary** to the ops run log: per source, `{ok, empty, failed, fell_back,
  diverged}` counts. This is your Volume/Freshness monitor. **Alert (even just a loud line in
  `dashboard.json`/run log) when the primary fell back or when divergences occurred** — so silent
  degradation can't hide. (P1)
- **Keep append-only + bitemporal**; ensure live re-runs dedup on `(series, key, as_of, fetched_at,
  source)` so a retried nightly job can't double-insert. (P2)

---

## Prioritized punch list for factor-scope

**P0 — correctness / silent-degradation (do first)**
1. `select_corroborated`: **flag-and-continue instead of `raise`** on divergence (quarantine the
   suspect value, keep primary, record both). Don't let one ETF kill the run.
2. Ensure **same adjustment basis** (raw vs adjusted close) across AkShare/Baostock before comparing —
   removes the dominant false-positive.
3. Replace **`except Exception: return []`** with specific catches + logging + empty-vs-failed
   distinction.
4. Add a **`source` tag** to every price Reading (provenance).

**P1 — robustness / observability**
5. **Asset-class-aware tolerance in bps** (0.5% default, 1.0% equity, 0.2% MMF), configurable per fund.
6. **Batch-level circuit breaker:** fail the run only if the divergence/failure *rate* is high (~>10%).
7. **Per-run ingestion summary + alert** on fall-back / divergence (Volume + Freshness monitoring).
8. **Retry with exponential backoff + full jitter** and per-source **timeout** in each live read.

**P2 — when scale/sources grow**
9. With **Mootdx (#21)** → **median-of-3 + MAD outlier flag** instead of primary/corroborator.
10. **Idempotent re-runs**: dedup key `(series, key, as_of, fetched_at, source)`.

---

## Sources (deduped, grouped)

**Failover / resilience:** martinfowler.com/bliki/CircuitBreaker.html · resilience4j.readme.io ·
aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter · aws.amazon.com/builders-library/
timeouts-retries-and-backoff-with-jitter · brooker.co.za/blog/2022/02/28/retries.html ·
aws.amazon.com/builders-library/avoiding-fallback-in-distributed-systems · barroso.org/publications/
TheTailAtScale.pdf · grpc.io/docs/guides/request-hedging · aerospike.com/blog/predictable-performance-
fan-out-architectures · exegy.com/design-patterns-market-data-part-3 · ice.com consolidated-feed

**Reconciliation / tolerance:** greshamtech.com golden-source · thegoldensource.com/market-data ·
resources.clearconsensus.com · ice.com market-price-validation · bloomberg.com evaluated-pricing ·
damadmbok.org · sec.gov/comments/s7-03-13/s70313-106.pdf · ey.com CSSF 24/856 · maples.com CSSF ·
cliffordchance.com CSSF 24 · investmentexecutive.com IFIC · fundrecs.com price-reconciliation ·
stonex.com basis-points · isda.org Portfolio-Reconciliation-SOP · eodhd.com adjusted-vs-close ·
riazarbi.github.io backtesting-adjusting-prices · etf.com understanding-net-asset-value ·
hausetutorials.netlify.app MAD outlier detection · medium/towards-data-engineering Fail-Fast-or-
Quarantine · gist victor-antoniassi silver-layer · datadoghq.com dbt-data-quality

**Error-handling / observability / lineage / PIT:** realpython.com diabolical-antipattern ·
google.github.io/styleguide/pyguide.html · docs.astral.sh/ruff/rules/blind-except · peps.python.org/
pep-0760 · apollographql.com/blog/using-nullability-in-graphql · sre.google/workbook/alerting-on-slos ·
montecarlodata.com 5-pillars · airbyte.com idempotency-in-data-pipelines · martinfowler.com/articles/
bitemporal-history.html · docs.arcticdb.io · github.com/man-group/ArcticDB · vbase.com point-in-time ·
juxt.pro value-of-bitemporality · aws Well-Architected AG.DLM.8 · secoda.co data-provenance

**CN-source fragility:** github.com/akfamily/akshare issues #6092/#7097/#7011 · pypi.org/project/
akshare-proxy · tushare.pro/document · github.com/mootdx/mootdx

> **Lowest-confidence items to re-verify if load-bearing:** exact CSSF 24/856 per-sub-class mapping
> (claim from law-firm summaries, not primary text); the August-2025 Tushare outage and exact Tushare
> point thresholds (single secondary source); survivorship-bias 1.5-2.0%/yr figure (single source).
