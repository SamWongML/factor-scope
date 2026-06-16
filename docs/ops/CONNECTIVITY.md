# Feed connectivity

A record of every external feed the live (online) path reaches, the result of triggering each one,
and what it takes to make each reachable. The canary that re-checks this is `make live-check`
(`tests/integration/test_adapters_live.py`, gated on `FACTOR_SCOPE_LIVE=1`); this page explains the
*environment* each leg needs around that canary.

## How the live path is selected

Online is the default; offline is opted into. The switch is `FACTOR_SCOPE_OFFLINE` (truthy unless
`""`/`"0"`, `config.py`) or the `--offline` flag on `run`/`nightly`/`ingest`/`discover`. Offline maps
`source="fixtures"` + `provider="fake"`; online maps `source="live"` + `provider="claude_code"`.
Every adapter has a lazy-imported `fetch_live` (online) and a `load_fixture` (offline) backend; the
branch lives in `markets/ashare.py` and `pipeline.py`.

## The feeds

Two transport classes, because the remediation differs sharply between them.

### HTTPS feeds (reachable through an egress allowlist)

| Feed | Adapter | Library | Hosts (port 443) | Credential |
|------|---------|---------|------------------|------------|
| Prices / NAV (primary) | `ingest/prices.py` | akshare → EastMoney | `push2his.eastmoney.com`, `push2delay.eastmoney.com`, `88.push2.eastmoney.com`, `quote.eastmoney.com` | — |
| ETF scale (AUM/shares) | `ingest/etf_scale.py` | akshare → EastMoney | `push2delay.eastmoney.com`, `push2his.eastmoney.com`, `88.push2.eastmoney.com`, `quote.eastmoney.com` | — |
| Fund holdings | `ingest/fund_holdings.py` | akshare → EastMoney | `api.fund.eastmoney.com`, `fundf10.eastmoney.com` | — |
| Fund universe | `ingest/fund_universe.py` | akshare → EastMoney | `api.fund.eastmoney.com`, `fund.eastmoney.com`, `fundf10.eastmoney.com`, `overseas.1234567.com.cn`, `help.1234567.com.cn` | — |
| Fundamentals (basket PE) | `ingest/fundamentals.py` | akshare → CSI | `www.csindex.com.cn`, `oss-ch.csindex.com.cn` | — |
| Trading activity | `ingest/trading_activity.py` | akshare → EastMoney | `push2his.eastmoney.com`, `push2delay.eastmoney.com`, `88.push2.eastmoney.com`, `quote.eastmoney.com` | — |
| Demand (industrial YoY) | `ingest/demand.py` | akshare → macro | `data.stats.gov.cn`, `datacenter-web.eastmoney.com`, `data.eastmoney.com`, `data.mofcom.gov.cn`, `finance.sina.com.cn`, `quotes.sina.cn`, `datacenter.jin10.com`, `datacenter-api.jin10.com`, `cdn.jin10.com` | — |
| FRED macro/liquidity | `ingest/fred.py` | fredapi | `api.stlouisfed.org` | `FRED_API_KEY` |
| EDGAR 13F / N-PORT | `ingest/edgar.py` | edgartools | `www.sec.gov`, `data.sec.gov`, `efts.sec.gov` | EDGAR identity (`EDGAR_IDENTITY` / `EDGARTOOLS_IDENTITY="name you@example.com"`) |
| Text stream (corpus) | `ingest/textstream.py` | httpx | the configured `--feed-url` host | — |
| DeepSeek (chore only) | `digest/deepseek.py` | httpx | `api.deepseek.com` | `DEEPSEEK_API_KEY` |

### Raw-TCP feeds (NOT reachable through an HTTP egress proxy)

| Feed | Adapter | Library | Endpoint |
|------|---------|---------|----------|
| Prices (2nd leg, cross-check) | `ingest/baostock.py` | baostock | `public-api.baostock.com:10030` (binary TCP) |
| Prices (3rd leg, cross-check) | `ingest/mootdx.py` | mootdx / pytdx | dozens of bare TDX IPs on `:7709` (binary TCP) |

The digest path (`digest/claude_code.py`) shells out to the local `claude` CLI, which uses
`ANTHROPIC_BASE_URL` and is not a feed. `positions`/`calls`/`themes` have no live data backend.

## Scan result — every feed currently fails

Triggered each adapter's `fetch_live` directly (and `make live-check`). All 12 live smoke tests fail.
The environment runs behind a TLS-terminating egress gateway (cert issuer
`O=Anthropic; CN=Egress Gateway SDS Issuing CA`) that enforces a **host allowlist**, and no feed host
is on it.

| Feed | Observed failure | Root cause |
|------|------------------|------------|
| prices / trading_activity / demand / fund_holdings | `JSONDecodeError` (akshare parsed the deny page as JSON) | egress 403 `host_not_allowed` |
| etf_scale / fundamentals | `HTTPError: 403 Forbidden` | egress 403 `host_not_allowed` |
| fred | `ValueError: You need to set a valid API key` | `FRED_API_KEY` unset (host also blocked) |
| edgar (13F + N-PORT) | `IdentityNotSetException` | EDGAR identity unset (host also blocked) |
| baostock | login failure / hang | `:10030` raw TCP dropped by the HTTP-only gateway |
| mootdx | no server reachable | `:7709` raw TCP dropped by the HTTP-only gateway |

The deny is uniform and unambiguous — even `https://www.google.com` returns:

```
HTTP/2 403
x-deny-reason: host_not_allowed
Host not in allowlist: www.google.com. Add this host to your network egress settings to allow access.
```

## Remediation

1. **Allowlist the HTTPS feed hosts** in the environment's network egress settings (see
   https://code.claude.com/docs/en/claude-code-on-the-web). The full set is the "HTTPS feeds" table
   above. The deny page itself names the exact host to add on each call, so allowlist iteratively if
   preferred. This unblocks every akshare feed plus FRED and EDGAR's transport.

2. **Set the two credentials** the canary needs (already documented in `RUNBOOK.md`): `FRED_API_KEY`
   and an EDGAR identity. Without them those two legs fail before the network, allowlist or not.

3. **The two raw-TCP price legs (baostock, mootdx) cannot traverse an HTTP egress proxy** — they
   speak binary protocols on `:10030`/`:7709`, not HTTP, so an allowlist entry does nothing. Options:
   provision a network policy that permits raw L4 egress to those endpoints, or accept them staying
   down. The price path already degrades safely: `prices.select_reconciled` returns the single
   present source as-is, so funds stay **priced** off the akshare leg — only cross-source
   corroboration (the `divergence` flag) is lost, and `_check_price_health` does **not** trip, since
   a fund priced by one source is not "unreconciled."

4. **Re-run `make live-check`** after each change. Green there is the gate before a nightly trusts the
   live path.
