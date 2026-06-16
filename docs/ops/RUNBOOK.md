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
{"as_of": "2026-06-05", "started_at": "...Z", "ended_at": "...Z", "provider": "claude_code",
 "n_items": 6, "n_holdings": 2, "n_watchlist": 1, "n_emerging": 3, "n_abstain": 2,
 "n_calls_logged": 6, "output_path": "out/dashboard.json",
 "costs": [{"provider": "claude_code", "model": "opus", "calls": 12, "input_tokens": 9000,
            "output_tokens": 2400, "cost_usd": 1.84}],
 "cost_usd": 1.84, "month_to_date_usd": 5.46, "monthly_budget_usd": 20.0,
 "budget_exhausted": false, "digest_failures": []}
```

`n_calls_logged` is tomorrow's scoring fuel; `n_abstain` is how often the engine was too blind to
call (the abstain-when-blind guardrail). `digest_failures` is empty on a clean night; a non-empty
list means a seat call raised (a missing/slow `claude`, malformed JSON) **or** was throttled by the
budget guard, and that item was degraded to abstain rather than crashing the run — each entry carries
its `code` and `error`. `costs` is this run's spend rolled up per `(provider, model)` — the source of
creation behind every dollar; `cost_usd` is its total, `month_to_date_usd` the calendar month's
running total against `monthly_budget_usd`, and `budget_exhausted` flags a run the ceiling throttled.
On the fake provider all of these are empty/zero, so the fixtures log stays byte-for-byte. Tail it to
see whether the nightly job is running, what it cost, and whether any seat is failing.

### The dashboard history

Every run also records its artifact as `out/dashboards/<as_of>.json`, so past mornings stay
inspectable. The first recording of a night stands: a later run never rewrites an earlier night
(mirroring the append-only store). The index a frontend lists nights from is read in O(1) from a
materialized `index.json` catalog beside these files; the catalog is a cache the API rebuilds from
the night files if it is ever lost or unreadable, so it cannot drift. To backfill a night from
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

## Verifying the live path

The offline suite (`make check`) is forced onto fixtures + the `fake` provider, so it never imports a
live source — it cannot catch a dependency upgrade or an upstream API/schema drift that breaks a real
nightly. Run the live canary after **any dependency bump or adapter change**, before trusting the next
nightly:

```bash
make live-check    # FACTOR_SCOPE_LIVE=1 live smoke suite + a real `factor-scope ingest`
```

`tests/integration/test_adapters_live.py` (gated by `FACTOR_SCOPE_LIVE=1`, skipped everywhere else)
hits each source and asserts its **full payload schema** — keys, types, plausible ranges — so drift
fails here loudly instead of silently feeding the artifact. Run it on the Mac-mini host: several CN
feeds geo-block cloud runners, so a GitHub Actions live canary is unreliable and intentionally not
wired up. The `fred` and `edgar` legs need their credentials set first (`FRED_API_KEY` and an EDGAR
identity, e.g. `EDGARTOOLS_IDENTITY="you you@example.com"`); without them those two legs are the only
ones that can't run. A fund with no tracked-index mapping has no valuation read and reads
`valid=False` — expected, not a failure.

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

- **`fake`** (the offline mode): deterministic, free. CI and demos (`--offline`) use only this. It
  meters nothing, so the spend ledger stays empty and the budget never throttles it.
- **`claude_code`**: the real judgment path — headless `claude -p` running the bull/bear subagents
  (`.claude/agents/`) then synthesis. Each seat call's cost (input/output tokens + USD) is read from
  the stream-json `result` envelope and recorded as a `Usage` tagged with its provider + model.
- **DeepSeek** drives the research job — theme discovery (draft + judge) and the emerging re-rank.
  Its calls have no USD in the response, so they are priced from the per-model table
  (`Config.model_prices`, USD per 1M in/out tokens); add a line per model you charge. It is **not** a
  `--provider` value for the nightly judgment (`get_provider("deepseek")` errors with a pointer).

### Cost telemetry + the monthly budget guard

Every model call — the nightly seats, the re-rank, and the research job — books one `Usage`
(`provider`, `model`, tokens, USD) into an append-only **spend ledger** (`--spend-path`, default
`out/spend.jsonl`). This is the constant record contract: switching a model changes only the `model`
field, so every dollar still traces to who produced it. Each nightly `RunRecord` also embeds its own
run's rollup (see *The run log*).

Set a monthly ceiling with `Config.monthly_budget_usd` (default `None` = unlimited). The guard reads
the ledger's **month-to-date** (the calendar month of the run's `as_of`, across *all* jobs) and, once
the running total is crossed, **gracefully throttles**: the nightly's lower-priority items degrade to
abstain-with-error `"monthly budget exhausted"` (holdings → watchlist → emerging order) and discovery
stops assessing further themes. The run completes on a **partial-but-valid** artifact and the record
sets `budget_exhausted: true` — the cap lives outside the model, exactly like the trend gate.

## Morning review

Read `dashboard.json` (or `factor-scope run` / `nightly` prints a terminal render). Most mornings the
right action is none — *patience is a position*. The engine shortens your **lag**, not your judgment.

## Documented, not built

Add only when its trigger arrives — not on the nightly critical path:

- An **optional local vector store** (e.g. LanceDB + local embeddings) for fuzzy theme/news recall —
  the emerging funnel's live discovery swap — live by default, never in CI.
