# RUNBOOK — nightly operations

How to run the engine every night and review the artifact every morning. The engine is a plain CLI
nightly batch: cross-platform core, scheduling behind a thin adapter. **It never
places orders** — it emits one dated `dashboard.json` a human reviews.

## The one-shot job

```bash
factor-scope nightly        # ingest → compute → digest → write dashboard.json (+ run log)
```

`nightly` is the production entrypoint. Unlike `run`, it defaults to a **durable** store, so each
night's leans persist as falsifiable calls that the next night's self-scoring loop scores. It
writes three things under `out/` (override with the flags below):

| Artifact | Default path | What it is |
|----------|--------------|------------|
| `dashboard.json` | `out/dashboard.json` | the morning artifact you review (the contract) |
| history | `out/dashboards/` | one immutable `<as_of>.json` per night (below) |
| store | `out/store.duckdb` | the append-only point-in-time store (readings + logged calls) |
| graph | `out/graph.ladybug` | the durable holdings look-through graph |
| run log | `out/nightly.jsonl` | one append-only ops record per run (below) |

Flags: `--output`, `--history-dir`, `--store-path`, `--graph-path`, `--log-path`, `--provider`,
`--as-of`, `--offline`, `--quiet`. The job is **online by default** (live sources + the real provider);
`--offline` (or `FACTOR_SCOPE_OFFLINE=1`) selects fixtures + the deterministic `fake` provider for a
demo or test run. Re-running the **same night is idempotent**: positions are stamped
with the run's `as_of`, so a second run that night re-uses the night's readings — the artifact stays
byte-for-byte and calls are never double-counted. A **new** night ingests fresh data.

### The run log

Each run appends one JSON line to `--log-path` (operations telemetry, *not* the artifact — so
wall-clock timestamps are fine here; only `dashboard.json` stays clock-free):

```json
{"as_of": "2026-06-05", "started_at": "...Z", "ended_at": "...Z", "provider": "fake",
 "n_items": 6, "n_holdings": 2, "n_watchlist": 1, "n_emerging": 3, "n_abstain": 2,
 "n_calls_logged": 6, "output_path": "out/dashboard.json", "cost_note": "...",
 "digest_failures": []}
```

`n_calls_logged` is tomorrow's scoring fuel; `n_abstain` is how often the engine was too blind to
call (the abstain-when-blind guardrail). `digest_failures` is empty on a clean night; a non-empty
list means a seat call raised (a missing/slow `claude`, malformed JSON) and that item was degraded
to abstain rather than crashing the run — each entry carries its `code` and `error`. On the fake
provider it is always empty. Tail it to see whether the nightly job is running and whether any seat
is failing.

### The dashboard history

Every run also records its artifact as `out/dashboards/<as_of>.json`, so past mornings stay
inspectable. The first recording of a night stands: a later run never rewrites an earlier night
(mirroring the append-only store). The index a frontend lists nights from is derived live from
these files by the API below — there is no persisted manifest to drift. To backfill a night from
before the history existed, rebuild it from the durable store:
`factor-scope run --as-of YYYY-MM-DD --store-path out/store.duckdb` (point-in-time reads see only
what was knowable that night; live-provider digests are not reproduced — cached debates are reused).

Serve the history to a frontend with the read-only API (the pinned `serve` extra):

```bash
factor-scope serve                      # http://127.0.0.1:8765
# GET /dashboards            → the index (one entry per night, oldest first)
# GET /dashboards/2026-06-05 → that night's artifact (immutable, cacheable forever)
# GET /dashboards/latest     → the newest night
# GET /openapi.json          → the typed schema to generate a frontend client from
```

It never ingests or reasons — the snapshot boundary holds. At one small JSON per night
(~365/year) the history needs no retention machinery.

## Scheduling

Generate the scheduler config with `factor-scope schedule` (a pure render — review it before
installing). It is **not** a daemon: it fires the one-shot job once a day.

### macOS — launchd (the Mac-mini production path)

```bash
factor-scope schedule --hour 22 --minute 0 --working-dir "$PWD" \
  -o ~/Library/LaunchAgents/com.factor-scope.nightly.plist
launchctl load ~/Library/LaunchAgents/com.factor-scope.nightly.plist   # enable
launchctl start com.factor-scope.nightly                               # optional: run now
launchctl unload ~/Library/LaunchAgents/com.factor-scope.nightly.plist # disable
```

The plist sets `RunAtLoad=false` (a scheduled batch, not a service) and `StartCalendarInterval` to
the given hour/minute. 22:00 local is the default — after the close, matching the artifact's
`generated_at` stamp.

### Linux — cron

```bash
factor-scope schedule --kind cron --hour 22 --minute 0 --working-dir "$PWD"
# → 0 22 * * * cd <dir> && factor-scope nightly >> out/nightly.out.log 2>> out/nightly.err.log
# add that line with: crontab -e
```

The scheduled job is **live** (online by default), so supply the source API keys (e.g.
`FRED_API_KEY`) via the launchd plist's `EnvironmentVariables` or the cron shell env. For a
fixtures-only dry run, add `--offline` to the generated command.

### Fund lifecycle dates on live data

No AkShare feed announces fund lifecycle events, so the two dates the guardrails read are sourced
indirectly. **Inception** (成立日期) comes from the exchange-traded fund ranking in the same
universe pull — every on-exchange fund carries one; an off-exchange fund's stays empty (missing
data never vetoes). **Delisting** is *disclosed by disappearance*: a fund the universe feed listed
before but not tonight gets an appended row delisted as of tonight, so old `as_of` reads still see
it alive (survivorship-aware both ways). Two consequences to know when reading the store: a feed
that returns nothing discloses nothing (an outage is not a mass death), and a fund a flaky feed
drops for one night reads delisted *that night only* — its fresh row the next night re-lists it.

## Provider & budget

- **`fake`** (the offline mode): deterministic, free. CI and demos (`--offline`) use only this.
- **`claude_code`**: the real judgment path — headless `claude -p` running the bull/bear subagents
  (`.claude/agents/`) then synthesis. From **2026-06-15**, `claude -p` meters against a separate
  **Agent-SDK credit**; the run log's `cost_note` flags this. Size the nightly run against that
  budget: roughly one bull + bear + synthesis per item (six items in the sample book).
- **DeepSeek** is a *chore* model (reformat/summarise evidence), off the judgment path — never a
  `--provider` value (`get_provider("deepseek")` errors with a pointer to the real options).

## Morning review

Read `dashboard.json` (or `factor-scope run` / `nightly` prints a terminal render). Most mornings the
right action is none — *patience is a position*. The engine shortens your **lag**, not your judgment.

## Graduate tier (documented, not built)

Add only when backtesting begins — not on the nightly critical path:

- **Bitemporal backtesting** on ArcticDB under a strict durability discipline: Deflated Sharpe Ratio,
  Probability of Backtest Overfitting, purged/embargoed CV (CPCV), parameter-stability, after-cost +
  point-in-time always. The self-scoring loop is the live durability mechanism until then.
- An **optional local vector store** (e.g. LanceDB + local embeddings) for fuzzy theme/news recall —
  the emerging funnel's live discovery swap — live by default, never in CI.
