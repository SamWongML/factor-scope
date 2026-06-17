# Live environment setup — handoff for local Claude Code

**Hand this file to Claude Code running on the machine that will execute the nightly** (the
Mac-mini production host). Open Claude Code in the `factor-scope` checkout and say:

> Read `docs/ops/LIVE_SETUP.md` and walk me through it interactively, one step at a time. Run the
> commands, check the result against the "Expect" line, and stop to help me fix anything that
> doesn't match before moving on.

The rest of this file is written for that Claude Code session.

---

## Context (for Claude Code)

factor-scope is a local-first nightly batch that emits one `out/dashboard.json`. It is **online by
default** — live data sources + the real `claude_code` provider; `--offline` / `FACTOR_SCOPE_OFFLINE=1`
selects fixtures + the deterministic `fake` provider. Your job here is to get the **live** path
working on this host and prove it, end to end.

A connectivity scan was already run in a cloud session and is recorded in
**`docs/ops/CONNECTIVITY.md`** — read it first. Its headline: in that cloud sandbox **every** feed was
blocked by an egress-gateway host allowlist. **That constraint is specific to the sandbox and does not
apply here** — this host has normal outbound network. On this host the only things that gate the live
path are (1) two credentials and (2) the CN feeds needing a network that can actually reach the
mainland China endpoints. Operational background lives in **`docs/ops/RUNBOOK.md`**.

The feeds, from `CONNECTIVITY.md`:
- **HTTPS, no credential:** prices, etf_scale, fund_holdings, fund_universe, fundamentals,
  trading_activity, demand — all `akshare` → EastMoney/CSI/macro hosts.
- **HTTPS, credential required:** FRED (`FRED_API_KEY`), EDGAR (an identity string).
- **Raw TCP (binary, not HTTP):** baostock (`public-api.baostock.com:10030`) and mootdx/pytdx
  (TDX servers on `:7709`) — the 2nd/3rd price cross-check legs. These need raw L4 egress; any
  HTTP-proxy-only network silently drops them.

Guide the user through the steps below **in order**. Don't skip the offline gate — it proves the core
works before any network is involved, so a later failure is unambiguously a connectivity/credential
issue, not a broken install.

---

## Step 0 — Prerequisites

```bash
uv --version          # the package manager; install from https://docs.astral.sh/uv/ if missing
claude --version      # the headless judgment provider; must be logged in (run `claude` once)
git rev-parse --abbrev-ref HEAD
```

**Expect:** `uv` and `claude` both report a version. If `claude` is missing or unauthenticated, the
nightly's digest seats fall back to abstain — install/log in before Step 5.

## Step 1 — Install with the live extras

```bash
make setup            # uv sync --frozen + dev,store,serve,live extras
```

**Expect:** a `.venv/` with `akshare`, `baostock`, `mootdx`, `edgartools`, `fredapi`, `httpx` present.
Verify: `uv run python -c "import akshare, baostock, mootdx, edgar, fredapi; print('live deps ok')"`.

## Step 2 — Prove the core offline (the gate before touching the network)

```bash
make check            # lint + typecheck + full offline suite — THE bar
make run              # build dashboard.json from fixtures and print it
```

**Expect:** `make check` green and `make run` prints a dashboard. If either fails, stop — fix the
install/toolchain here, because nothing live will work on a broken core.

## Step 3 — Set the two credentials

Only FRED and EDGAR need secrets; every other feed is keyless. Acquire and export:

- **`FRED_API_KEY`** — free from <https://fredaccount.stlouisfed.org/apikeys> (register → request key).
- **EDGAR identity** — no signup; SEC just requires a User-Agent of the form `name email`. Set
  **`EDGAR_IDENTITY`** — this is the variable the edgartools library reads (`EDGARTOOLS_IDENTITY`
  is not checked by the library and will have no effect).
- **`DEEPSEEK_API_KEY`** *(optional)* — only if you run the research/discovery job; not on the nightly
  judgment path.

For an interactive shell, put them in the shell profile (`~/.zshrc`):

```bash
export FRED_API_KEY="…"
export EDGAR_IDENTITY="Your Name you@example.com"
# export DEEPSEEK_API_KEY="…"   # optional, discovery only
```

Then `source ~/.zshrc`. **Expect:** `echo $FRED_API_KEY` and `echo $EDGARTOOLS_IDENTITY` non-empty.
(The scheduled job reads these from the launchd plist instead — see Step 6.)

## Step 4 — Verify connectivity, feed by feed

```bash
make live-check       # FACTOR_SCOPE_LIVE=1 live smoke suite + a real `factor-scope ingest`
```

`tests/integration/test_adapters_live.py` hits each source and asserts its full payload schema, so
drift or a dead host fails loudly here. **Expect:** all tests pass. If some fail, triage with this
table (full host list in `CONNECTIVITY.md`):

| Failing test(s) | Likely cause | Fix |
|---|---|---|
| `test_fred_live_smoke` → `valid API key` | `FRED_API_KEY` unset/typo | redo Step 3, re-`source` |
| `test_edgar_*` → `IdentityNotSetException` | EDGAR identity unset | set `EDGAR_IDENTITY` |
| any akshare test → `JSONDecodeError`/`403`/timeout | host unreachable from this network (CN geo-block, or an HTTP proxy in front) | run on a network that can reach EastMoney/CSI directly; if a corporate proxy intercepts, allowlist the hosts in `CONNECTIVITY.md` |
| `test_baostock_live_smoke` / `test_mootdx_live_smoke` → hang/login fail | raw-TCP `:10030`/`:7709` blocked | needs raw L4 egress (no HTTP-only proxy); confirm with `python -c "import socket; socket.create_connection(('public-api.baostock.com',10030),5)"` |

To probe a single feed instead of the whole suite, run e.g.
`FACTOR_SCOPE_LIVE=1 uv run --extra live python -m pytest tests/integration/test_adapters_live.py::test_prices_live_smoke -v`.

**Acceptable partial state:** if baostock/mootdx can't be reached but the akshare prices leg can, the
run still prices every fund — `prices.select_reconciled` returns the single present source as-is and
the price health check does **not** trip. You only lose cross-source corroboration. Don't block the
go-live on those two legs; do block on the akshare legs, FRED, and EDGAR.

## Step 5 — First live nightly

```bash
factor-scope nightly          # ingest → compute → digest → write out/dashboard.json (+ run log)
```

**Expect:** `out/dashboard.json` written, plus `out/nightly.jsonl` with a record whose
`digest_failures` is `[]` and `n_abstain` is plausible (not every item). Inspect the run log's `costs`
to confirm the `claude_code` seats actually ran. Re-running the same `as_of` is idempotent.

## Step 6 — Schedule it (macOS launchd)

```bash
factor-scope schedule --hour 22 --minute 0 --working-dir "$PWD" \
  -o ~/Library/LaunchAgents/com.factor-scope.nightly.plist
```

**Before loading**, add the credentials to the plist's `EnvironmentVariables` block (the launchd job
does not see your interactive shell's exports) — at minimum `FRED_API_KEY` and `EDGARTOOLS_IDENTITY`.
Then:

```bash
launchctl load  ~/Library/LaunchAgents/com.factor-scope.nightly.plist   # enable
launchctl start com.factor-scope.nightly                                # optional: run now
```

**Expect:** after a manual `start`, a fresh record appended to `out/nightly.jsonl`.

---

## Done when

- `make check` green, `make live-check` green (or green except the two raw-TCP legs, knowingly).
- `factor-scope nightly` produced a `dashboard.json` with a clean run-log record.
- The launchd job is loaded with credentials wired into its `EnvironmentVariables`.

Report back to the user with: which feeds are live, which (if any) are knowingly degraded and why, and
the path to the first artifact.
