"""Live-backend smoke tests for the ingestion adapters.

These hit the network and are **skipped unless ``FACTOR_SCOPE_LIVE=1``**, so CI never calls a live
source. They are the canary `make live-check` runs after any dependency/adapter change: each asserts
the adapter's *full* payload schema (keys, types, plausible ranges), so an upstream API/schema drift
fails here loudly before a nightly trusts it — not silently at runtime.

Beyond schema, the per-fund price legs assert the **windowed + incremental return contract** the
offline cassettes can only simulate: a cold pull seeds a *window* of bars (not just the latest — the
regression that returning one bar caused), and a re-pull with a ``since`` watermark returns only the
sessions past it. These are the live-path behaviors offline mode bypasses entirely via the cassette.
"""

import math
import os

import pytest

from factor_scope.config import Config
from factor_scope.ingest import (
    baostock,
    demand,
    eastmoney,
    edgar,
    etf_scale,
    fred,
    fund_holdings,
    fund_universe,
    fundamentals,
    mootdx,
    prices,
)
from factor_scope.ingest.feed import LiveFeed, get_feed
from factor_scope.store import DuckDBStore

pytestmark = pytest.mark.integration

_LIVE = os.environ.get("FACTOR_SCOPE_LIVE") == "1"
skip_unless_live = pytest.mark.skipif(not _LIVE, reason="set FACTOR_SCOPE_LIVE=1 to run live")

# A held on-exchange ETF used as the per-code probe across the price/holdings/valuation adapters.
_PROBE = "561010"

# A real run stamp (not the ``"t"`` sentinel) so the price legs derive a dated seed window rather
# than degrading to the backend's default full range — to exercise the seeded-window contract.
_RUN_STAMP = "2026-06-05T22:00:00Z"


@skip_unless_live
def test_eastmoney_kline_live_defeats_the_push2his_reset() -> None:
    # The reason the client exists: a real-Chrome TLS/HTTP fingerprint gets a daily window back from
    # push2his, where a plain ``requests`` handshake is reset. A non-empty window IS the
    # impersonation working; it also pins the domain-bar schema the NAV leg maps close→nav on.
    bars = eastmoney.kline(_PROBE, beg="20240101")
    assert len(bars) > 1  # a window came back — the reset was defeated, not an empty/dropped read
    assert all(bar.keys() == {"date", "close", "turnover", "amount"} for bar in bars)
    assert all(bar["close"] > 0 for bar in bars)
    stamps = [bar["date"] for bar in bars]
    assert stamps == sorted(stamps)  # oldest-first, the order select_reconciled's r[-1] relies on


@skip_unless_live
def test_prices_live_smoke() -> None:
    # The NAV leg maps the impersonating K-line client's bars (close → nav). The cold pull seeds a
    # *window* of bars, not just the latest, so the trend gate's 200-day MA has its own-history
    # distribution from night one — the regression returning one bar caused.
    floor = prices._floor(_RUN_STAMP, None)
    bars = eastmoney.kline(_PROBE, beg=prices._em_start(_RUN_STAMP, None))
    readings = prices.from_kline(_PROBE, bars, fetched_at=_RUN_STAMP, floor=floor)
    assert len(readings) > 1
    assert all(r.key == _PROBE for r in readings)
    assert all(r.payload.keys() == {"nav", "source"} for r in readings)
    assert all(r.payload["nav"] > 0 for r in readings)
    assert all(r.payload["source"] == prices.SOURCE for r in readings)
    # Bars arrive oldest-first: select_reconciled takes r[-1] as the latest, so the order matters.
    stamps = [r.as_of for r in readings]
    assert stamps == sorted(stamps)
    # The window is bounded to the ~400-trading-day seed floor, not the fund's full history.
    assert floor is not None and all(r.as_of > floor for r in readings)


@skip_unless_live
def test_prices_incremental_since_live_smoke() -> None:
    # The other half of the contract: the real EastMoney ``beg`` path (the leg that tripped the rate
    # limiter when unbounded) returns only sessions past a watermark — a nightly re-pull is linear.
    cold = prices.from_kline(
        _PROBE,
        eastmoney.kline(_PROBE, beg=prices._em_start(_RUN_STAMP, None)),
        fetched_at=_RUN_STAMP,
        floor=prices._floor(_RUN_STAMP, None),
    )
    assert len(cold) > 1
    since = cold[len(cold) // 2].as_of  # a real session mid-window becomes the watermark
    incremental = prices.from_kline(
        _PROBE,
        eastmoney.kline(_PROBE, beg=prices._em_start(_RUN_STAMP, since)),
        fetched_at=_RUN_STAMP,
        floor=since,
    )
    assert all(r.as_of > since for r in incremental)  # only sessions strictly past the watermark
    assert len(incremental) < len(cold)  # a strict subset of the window, not a whole re-fetch
    assert all(r.payload.keys() == {"nav", "source"} for r in incremental)


@skip_unless_live
def test_baostock_live_smoke() -> None:
    # the Baostock cross-validation leg — a window matching the AkShare leg, not just the latest bar
    readings = baostock.fetch_live(_PROBE, fetched_at=_RUN_STAMP)
    assert len(readings) > 1
    assert all(r.payload.keys() == {"nav", "source"} for r in readings)
    assert all(r.payload["nav"] > 0 for r in readings)


@skip_unless_live
def test_mootdx_live_smoke() -> None:
    # the third (TDX) cross-validation leg — a count-based window matching the other two legs. This
    # is the canary for the hardened client: a pinned server + bounded socket means it returns fast
    # or degrades, never wedging the run (the live-ingest hang this leg used to cause).
    readings = mootdx.fetch_live(_PROBE, fetched_at=_RUN_STAMP)
    assert len(readings) > 1
    assert all(r.key == _PROBE for r in readings)
    assert all(r.payload.keys() == {"nav", "source"} for r in readings)
    assert all(r.payload["nav"] > 0 for r in readings)
    assert all(r.payload["source"] == mootdx.SOURCE for r in readings)
    # Bars arrive oldest-first: select_reconciled takes r[-1] as the latest, so the order matters.
    stamps = [r.as_of for r in readings]
    assert stamps == sorted(stamps)


@skip_unless_live
def test_mootdx_incremental_since_live_smoke() -> None:
    # The other half of the contract, matching the AkShare/Baostock legs: a re-pull past a watermark
    # returns only newer sessions, so the nightly re-pull stays a strict subset, not a re-fetch.
    cold = mootdx.fetch_live(_PROBE, fetched_at=_RUN_STAMP)
    assert len(cold) > 1
    since = cold[len(cold) // 2].as_of  # a real session mid-window becomes the watermark
    incremental = mootdx.fetch_live(_PROBE, fetched_at=_RUN_STAMP, since=since)
    assert all(r.as_of > since for r in incremental)  # only sessions strictly past the watermark
    assert len(incremental) < len(cold)  # a strict subset of the window, not a whole re-fetch
    assert all(r.payload.keys() == {"nav", "source"} for r in incremental)


@skip_unless_live
def test_fred_live_smoke() -> None:
    reading = fred.fetch_live("DGS10", fetched_at="t")[0]
    assert reading.payload.keys() == {"series_id", "value"}
    assert reading.payload["series_id"] == "DGS10"
    assert isinstance(reading.payload["value"], float)


@skip_unless_live
def test_fund_holdings_live_smoke() -> None:
    # AkShare queries holdings per calendar year, derived from the run stamp — pass a real one.
    reading = fund_holdings.fetch_live(_PROBE, fetched_at=_RUN_STAMP)[0]
    assert reading.payload.keys() == {"fund", "holding", "weight"}
    assert reading.payload["fund"] == _PROBE
    assert 0.0 <= reading.payload["weight"] <= 1.0


@skip_unless_live
def test_fund_universe_live_smoke() -> None:
    readings = fund_universe.fetch_live(
        etf_scale.fetch_spot_board(), as_of="2026-06-05", fetched_at="t"
    )
    assert readings and "on_exchange" in readings[0].payload  # all funds, ETFs marked on-exchange
    # the launch-at-peak guardrail needs real launch dates: on-exchange funds carry one live
    assert any(r.payload["on_exchange"] and r.payload["inception"] for r in readings)


@skip_unless_live
def test_etf_scale_live_smoke() -> None:
    reading = etf_scale.fetch_live(etf_scale.fetch_spot_board(), fetched_at="t")[0]
    # ``amount`` (成交额, the liquidity leg of the universe tier) rides on the same spot board
    assert reading.payload.keys() == {"exchange", "aum", "shares", "amount"}
    assert reading.payload["exchange"] in {"sse", "szse"}
    assert reading.payload["aum"] > 0 and reading.payload["shares"] > 0
    assert reading.payload["amount"] >= 0  # a fund can trade nothing on a given day


@skip_unless_live
def test_demand_live_smoke() -> None:
    readings = demand.fetch_live(fetched_at="t")
    assert readings and {r.key for r in readings} == {demand.KEY}  # one book-wide series
    assert readings[0].payload.keys() == {"revision"}
    # finite, not NaN — confirms the unreleased-forecast row was dropped, not carried through
    assert math.isfinite(readings[0].payload["revision"])


@skip_unless_live
def test_fundamentals_live_smoke() -> None:
    reading = fundamentals.fetch_live(_PROBE, fetched_at="t")[0]  # 561010 is in the tracked map
    assert reading.key == _PROBE
    assert reading.payload.keys() == {"pe"}
    assert reading.payload["pe"] > 0
    # a fund with no tracked-index mapping degrades to no rows, never raising
    assert fundamentals.fetch_live("000000", fetched_at="t") == []


@skip_unless_live
def test_trading_activity_live_drives_the_feed_and_one_pull_feeds_both_legs(monkeypatch) -> None:
    # The production path, not a direct mapper call: feed.activity → _em → spot-vs-deep → the
    # browser-impersonating K-line client. A cold code (empty store) deep-pulls ONE push2his window
    # that feeds BOTH legs — so a deep-pull fund costs one request, not two — and the run's
    # configured impersonation profile reaches the real client. A window of {turnover, amount} bars
    # (not the single provisional spot-board bar the fallback yields when impersonation fails)
    # proves the reset was defeated on the live activity path.
    real_kline = eastmoney.kline
    seen: dict = {"calls": 0}

    def counting_kline(code, *, beg, impersonate="chrome"):
        seen["calls"] += 1
        seen["impersonate"] = impersonate
        return real_kline(code, beg=beg, impersonate=impersonate)

    monkeypatch.setattr(eastmoney, "kline", counting_kline)
    config = Config(source="live", live_pacing_seconds=0.0)
    store = DuckDBStore(":memory:")  # empty → the probe reads cold → one deep pull seeds both legs
    try:
        feed = get_feed(config, store)
        assert isinstance(feed, LiveFeed)  # a live config selects the store-aware online feed
        activity = feed.activity(_PROBE, fetched_at=_RUN_STAMP)  # the universe-loop entry
        nav = feed._em(_PROBE, fetched_at=_RUN_STAMP).nav  # the price loop reuses the SAME memo
    finally:
        store.close()
    assert seen["calls"] == 1  # one push2his pull fed both legs, not one per leg
    assert seen["impersonate"] == config.eastmoney_impersonate  # the configured profile threaded
    assert len(activity) > 1  # a settled-history window via impersonation, not the spot fallback
    assert all(r.key == _PROBE for r in activity)
    assert all(r.payload.keys() == {"turnover", "amount"} for r in activity)  # history, untagged
    assert all(r.payload["turnover"] >= 0 and r.payload["amount"] >= 0 for r in activity)
    stamps = [r.as_of for r in activity]
    assert stamps == sorted(stamps)  # oldest-first, matching the NAV leg's window contract
    assert [r.as_of for r in nav] == stamps  # both legs are the one shared window — same sessions


@skip_unless_live
def test_edgar_13f_live_smoke() -> None:
    reading = edgar.fetch_live("0001067983", fetched_at="t")[0]  # Berkshire Hathaway 13F-HR
    assert reading.payload.keys() == {"filer", "holding", "shares"}
    assert reading.payload["shares"] > 0


@skip_unless_live
def test_edgar_nport_live_smoke() -> None:
    reading = edgar.fetch_live("0000036405", form="NPORT-P", fetched_at="t")[0]  # Vanguard 500
    assert reading.payload.keys() == {"filer", "holding", "weight"}
    assert 0.0 <= reading.payload["weight"] <= 1.0
