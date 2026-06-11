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
| store | `out/store.duckdb` | the append-only point-in-time store (readings + logged calls) |
| graph | `out/graph.ladybug` | the durable holdings look-through graph |
| run log | `out/nightly.jsonl` | one append-only ops record per run (below) |

Flags: `--output`, `--store-path`, `--graph-path`, `--log-path`, `--provider`, `--as-of`,
`--offline`, `--quiet`. The job is **online by default** (live sources + the real provider);
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

### Live-feed gaps

AkShare's fund-universe feed does not yet disclose **inception** or **delisting** dates — live rows
ingest both empty. The guardrails degrade safely (no positive evidence → no veto), but two reads
are inert on live data until the feed is enriched (the U06 data-engineering item, `docs/ROADMAP.md`
§3): the **launch-at-peak** veto never fires, and every fund reads as still listed, so the
survivorship-aware membership test only bites on a feed that carries delisting dates. The
**overheated** veto is unaffected — its run-up and PE-percentile inputs come from the live price
and fundamentals feeds.

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
