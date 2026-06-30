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
| series | `out/series/` | one compact `<code>.json` per-fund time-series trail (below) |
| store | `out/store.duckdb` | the append-only point-in-time store (readings + logged calls) |
| replica | `out/store.replica.duckdb` | a read-only copy of the store, refreshed after each run (below) |
| graph | `out/graph.ladybug` | the durable holdings look-through graph |
| run log | `out/nightly.jsonl` | one append-only ops record per run (below) |

Flags: `--output`, `--history-dir`, `--series-dir`, `--store-path`, `--replica-path`,
`--graph-path`, `--log-path`, `--provider`, `--as-of`, `--offline`, `--quiet`, `--deadline`. The job is **online by default** (live sources + the real provider);
`--offline` (or `FACTOR_SCOPE_OFFLINE=1`) selects fixtures + the deterministic `fake` provider for a
demo or test run. Re-running the **same night is idempotent**: positions are stamped
with the run's `as_of`, so a second run that night re-uses the night's readings — the artifact stays
byte-for-byte and calls are never double-counted. A **new** night ingests fresh data.

**`--deadline <seconds>` — the run-level wall-clock backstop.** Unset by default (unbounded). Every
source read is already bounded individually (a 20s per-attempt deadline × 3 retries; the Mootdx TDX
leg additionally pins a server, sets a socket timeout, and probes liveness so it degrades rather than
hangs), so no single leg stalls forever. But the **aggregate** of a *cold-start full-universe* run is
long — hundreds of funds × several per-fund legs, paced under the EastMoney rate limiter, so tens of
minutes is normal. Set `--deadline` on the first cold start (and, if you want a hard nightly SLA, on
the cron job) to cap the whole gather: once exceeded the per-fund/per-code loops stop and the
**partial-but-valid** artifact still ships — the price circuit-breaker then reasons only over the
codes actually reached, and any unreached book codes are logged loudly rather than passing as
healthy. `0` or unset = unbounded. Incremental nightly re-pulls (a warm store) are fast — the
watermark fetches only sessions past the floor — so the deadline matters mainly for the cold start.

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
# GET /dashboards?limit=&offset= → the index, oldest first (bounded page; X-Total-Count + Link)
# GET /dashboards/2026-06-05     → that night's artifact (immutable, cacheable forever; ETag)
# GET /dashboards/latest         → the newest night (revalidates against a strong ETag)
# GET /series                    → the funds with a materialized trail (revalidates; ETag)
# GET /series/510300             → that fund's pre-materialized time-series (revalidates; ETag)
# GET /openapi.json              → the typed schema to generate a frontend client from
```

It never ingests or reasons — the snapshot boundary holds. At one small JSON per night
(~365/year) the history needs no retention machinery.

### The per-fund time-series (gold)

Every run also appends one compact point — NAV, return, gate, factor bands — to each fund's
`out/series/<code>.json` trail, so a chart serves the whole history from that static, cacheable
file with **no query in the request path**: the read is flat in the store's size. Like the night
history, the first point recorded for an `as_of` stands, so a re-run never rewrites it.

### Isolated ad-hoc queries (the read-only replica)

The common charting case is the pre-materialized series above; for genuine ad-hoc reads, the
nightly job publishes a **read-only file replica** of the store to `--replica-path` after each run
(the writer is closed by then, so the file is checkpointed). Open it with `ReadReplica`, which
queries the replica — never the live writer's handle, satisfying DuckDB's one-RW-or-many-RO rule
structurally — through a bounded connection pool with per-query memory, row, and time caps. The
replica defaults beside the store (`<store-path>.replica.duckdb`); override its location with
`--replica-path`.

The replica is the **hot** store file. With `--cold-dir` set, the older readings live in their own
read-only Parquet, so a `ReadReplica` query over the hot `readings` table sees only the hot window;
for the full point-in-time history, open the replica as `DuckDBStore(replica, read_only=True,
cold_dir=…)` instead — its `read_as_of`/`history` reads union hot + cold.

**Response hygiene.** Every response carries a strong `ETag` (immutable nights are also
content-addressed by `snapshot_id`) and is gzip-compressed over the wire; `/dashboards` is bounded
to one page (`limit` ≤ 500, default 100), the full count in `X-Total-Count` and page navigation in
`Link` — the list is never returned unbounded.

**CORS.** A localhost bind opens CORS wide (single-user surface); any remote bind
(`--host 0.0.0.0`) stays closed until the front-end origins are named explicitly:

```bash
factor-scope serve --host 0.0.0.0 --allow-origin https://dash.example
```

**Static / CDN tier (the common path off the app).** The dated artifacts are immutable and
content-addressed, so they are trivially cacheable — the dashboard directory *is* the origin. Front
it directly with a static file server / object store / CDN and let only the dynamic endpoints reach
the Python app:

```nginx
# the immutable, content-addressed nights — served straight off disk, long-lived + revalidatable
location /dashboards/ {
    root /srv/factor-scope/out;          # …/out/dashboards/<as_of>.json
    add_header Cache-Control "public, max-age=31536000, immutable";
    gzip on; gzip_types application/json;
    try_files $uri @app;                 # /dashboards (index) and /latest fall through to the app
}
location @app { proxy_pass http://127.0.0.1:8765; }
```

This takes the dominant read (open a given morning) entirely off the app process; only the
once-a-day index/`latest` touch Python.

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
wired up. The `fred` and `edgar` legs need their credentials first (`FRED_API_KEY` and an EDGAR
identity `EDGAR_IDENTITY="you you@example.com"`) — store them in the **Keychain** (below), and both
`make live-check` and the launchd nightly resolve them at runtime. A live `ingest`/`nightly`
**preflights** these before the universe pull and **fails fast in seconds** if `FRED_API_KEY` is
unset (or `EDGAR_IDENTITY` is unset while `edgar_ciks` is configured) — a permanent operator error
caught early, not after a multi-hour run. A *transient* outage on either feed instead degrades only
that leg to no reading (factor `valid=False`) and the run continues. A fund with no tracked-index
mapping has no valuation read and reads `valid=False` — expected, not a failure.

### Credentials (macOS Keychain — the single store live-check and the nightly share)

The two live keys are resolved **env first, then the macOS login Keychain** (service `factor-scope`,
account = the variable name; see `factor_scope/credentials.py`). This is the one place that works for
*both* contexts: an interactive `make live-check` inherits an exported var from the shell, but the
**launchd nightly sources no shell rc at all** (not `.zshrc`, not even `.zshenv`), so a dotfile export
never reaches it. Storing the secret in the Keychain — read at runtime — keeps it out of plaintext
dotfiles and out of the plist, and both contexts resolve it identically. Store each once:

```bash
security add-generic-password -A -s factor-scope -a FRED_API_KEY  -w 'your-fred-key'
security add-generic-password -A -s factor-scope -a EDGAR_IDENTITY -w 'You you@example.com'  # only if edgar_ciks is set
```

`-A` lets the local `security` binary read the item without an ACL prompt — required for the
non-interactive nightly, acceptable on a dedicated single-user host. Update a value by re-running with
`-U`. With the keys in the Keychain you can **remove them from `.zshrc`** — nothing else needs them.

### Running live from inside China (OpenClash / a transparent proxy)

From inside China the feeds split into two families that need **opposite** routing, and a single
global proxy can't serve both. With a transparent, rules-based proxy (OpenClash on the gateway) the
host sets no proxy env vars; every connection is routed DIRECT or through the overseas node **by
domain/GEOIP rule** — so the app needs no proxy configuration, only a *complete* ruleset. The legs:

| Feed (adapter) | Transport · AkShare fn | Host(s) | Route |
|---|---|---|---|
| prices · trading_activity · etf_scale · fund_holdings · fund_universe | `requests` · `fund_etf_hist_em` / `fund_etf_spot_em` / `fund_portfolio_hold_em` / `fund_name_em` / `fund_exchange_rank_em` | `*.eastmoney.com`, `1234567.com.cn`, `*.sina.com.cn` | **DIRECT** |
| fundamentals | `requests` · `stock_zh_index_value_csindex` | `csindex.com.cn` | **DIRECT** |
| demand | `requests` · `macro_china_industrial_production_yoy` | eastmoney datacenter / NBS / sina / jin10 mirrors | **DIRECT** |
| prices x-check (Baostock) | raw TCP `:10030` | `baostock.com` (+ its IP) | **DIRECT** |
| prices x-check (Mootdx/pytdx) | raw TCP `:7709`, `select_best_ip` | **bare CN IPs** + `tdx.*.com.cn` | **DIRECT** |
| fred | `urllib` | `api.stlouisfed.org` | **proxy node** |
| edgar | `httpx` | `www.sec.gov`, `efts.sec.gov`, `data.sec.gov` | **proxy node** |

The CN HTTP feeds all sit under domains in `geosite:cn`; the overseas legs fall through to the node.
The one trap is the **raw-socket** price cross-checks: Mootdx probes *bare* CN IPs (no domain for a
`DOMAIN-SUFFIX` rule to match) and Baostock connects by IP too, so they need a **`GEOIP,CN,DIRECT`**
rule — without it `test_mootdx_live_smoke` fails (Mootdx going dark is non-fatal for a real run,
which reconciles prices from AkShare + Baostock, but the canary flags it). A working ruleset, ordered
first-match-wins:

```yaml
- GEOIP,CN,DIRECT                       # raw-socket CN feeds (Mootdx bare IPs, Baostock) — no domain to match
- DOMAIN-SUFFIX,baostock.com,DIRECT     # belt-and-suspenders for domains geosite:cn may miss on a cold load
- DOMAIN-SUFFIX,csindex.com.cn,DIRECT
- DOMAIN-SUFFIX,eastmoney.com,DIRECT
- DOMAIN-SUFFIX,1234567.com.cn,DIRECT
- DOMAIN-SUFFIX,sina.com.cn,DIRECT
- RULE-SET,cn,DIRECT                    # geosite:cn — the primary CN lever
# FRED (stlouisfed.org) and SEC (sec.gov) fall through to the overseas node via the existing catch-all
```

Read a `make live-check` failure as a routing diagnosis: a red CN smoke test ⇒ that host isn't going
DIRECT (a missing rule, or `GEOIP,CN,DIRECT` for Mootdx); a FRED/EDGAR failure ⇒ the node isn't
reaching the overseas host, or the Keychain entry is missing/locked.

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

The job resolves `FRED_API_KEY` / `EDGAR_IDENTITY` from the **Keychain** at runtime (above), so no
secret is baked into the plist. The plist sets `RunAtLoad=false` (a scheduled batch, not a service)
and `StartCalendarInterval` to the given hour/minute. 22:00 local is the default — after the close,
matching the artifact's `generated_at` stamp. `factor-scope` is baked in by **absolute path**
(resolved at render time), so launchd's minimal `PATH` finds it.

The scheduled job is **live** (online by default) and **sources no shell rc files** — launchd does
not read `~/.zshrc` (it is interactive-only) or `~/.zshenv`, so an exported key the host can see at
the terminal does **not** reach the job; the live keys come from the **Keychain** at runtime (above)
instead. For a fixtures-only dry run, add `--offline` to the generated command.

A scheduled live job runs with no `--as-of`, so it reasons as-of the **run date** — the host's
**local** date (resolved once per run and frozen, so a multi-hour pull that crosses midnight stays
one consistent night). Run the Mac-mini in the market's timezone (Asia/Shanghai) so the run date
matches the trading day; the 22:00 fire time is already local. Pass `--as-of YYYY-MM-DD` only to
backfill a specific night.

### The morning resume (quota-deferred items)

The judgment seats run on a subscription plan whose usage allowance refills on a rolling ~5-hour
window. If the nightly spends it before every item is argued, the unfinished items are **deferred**,
not guessed: each is left **uncommitted** (no call-of-record) and marked `digest_status: deferred`
in the artifact — a *pending* state, never read as an abstain (a real "no bet"). Holdings are argued
first, so a cut falls on watchlist/emerging, and once the allowance is spent the run **circuit-breaks**
the rest (no doomed seat calls into a closed window) and finishes on a partial-but-valid artifact.

Install the **resume** companion to finish them once the window resets, before the open:

```bash
factor-scope schedule --job resume --hour 5 --minute 30 --working-dir "$PWD" \
  -o ~/Library/LaunchAgents/com.factor-scope.nightly.resume.plist
launchctl load ~/Library/LaunchAgents/com.factor-scope.nightly.resume.plist
```

The resume is a plain nightly re-run for the same night: ingest is skipped and the already-decided
items are served from the durable debate cache, so only the deferred items are argued and committed
— in time to be scored at the open. It is idempotent: a re-run with nothing pending is a quick no-op,
and a committed call is never rewritten (a held item's call stays immutable). The resume is sealed to
the scoring window — once a forward price exists for a night, no new call is committed for it, so a
late or stale re-run can never back-fill a hindsight-dated call.

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
  the `--output-format json` envelope and recorded as a `Usage` tagged with its provider + model.
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
the running total is crossed, **gracefully throttles**: the nightly's lower-priority items defer
(holdings → watchlist → emerging order) and discovery stops assessing further themes. A budget-capped
item is treated exactly like a quota-deferred one — left **uncommitted** (no call-of-record) and
marked `digest_status: deferred` rather than recorded as a "no bet", so an unspent decision never
pollutes the self-scoring record. The run completes on a **partial-but-valid** artifact and the record
sets `budget_exhausted: true` — the cap lives outside the model, exactly like the trend gate.

## Morning review

Read `dashboard.json` (or `factor-scope run` / `nightly` prints a terminal render). Most mornings the
right action is none — *patience is a position*. The engine shortens your **lag**, not your judgment.

## Documented, not built

Add only when its trigger arrives — not on the nightly critical path:

- An **optional local vector store** (e.g. LanceDB + local embeddings) for fuzzy theme/news recall —
  the emerging funnel's live discovery swap — live by default, never in CI.
