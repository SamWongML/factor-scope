"""Live-backend smoke tests for the ingestion adapters.

These hit the network and are **skipped unless ``FACTOR_SCOPE_LIVE=1``**, so CI never calls a live
source. They are the canary `make live-check` runs after any dependency/adapter change: each asserts
the adapter's *full* payload schema (keys, types, plausible ranges), so an upstream API/schema drift
fails here loudly before a nightly trusts it — not silently at runtime.
"""

import math
import os

import pytest

from factor_scope.ingest import (
    baostock,
    demand,
    edgar,
    etf_scale,
    fred,
    fund_holdings,
    fund_universe,
    fundamentals,
    mootdx,
    prices,
    trading_activity,
)

pytestmark = pytest.mark.integration

_LIVE = os.environ.get("FACTOR_SCOPE_LIVE") == "1"
skip_unless_live = pytest.mark.skipif(not _LIVE, reason="set FACTOR_SCOPE_LIVE=1 to run live")

# A held on-exchange ETF used as the per-code probe across the price/holdings/valuation adapters.
_PROBE = "561010"


@skip_unless_live
def test_prices_live_smoke() -> None:
    reading = prices.fetch_live(_PROBE, fetched_at="t")[0]
    assert reading.key == _PROBE
    assert reading.payload.keys() == {"nav", "source"}
    assert reading.payload["nav"] > 0
    assert reading.payload["source"] == prices.SOURCE


@skip_unless_live
def test_baostock_live_smoke() -> None:
    reading = baostock.fetch_live(_PROBE, fetched_at="t")[0]  # the Baostock cross-validation leg
    assert reading.payload["nav"] > 0


@skip_unless_live
def test_mootdx_live_smoke() -> None:
    reading = mootdx.fetch_live(_PROBE, fetched_at="t")[0]  # the third (TDX) cross-validation leg
    assert reading.payload["nav"] > 0


@skip_unless_live
def test_fred_live_smoke() -> None:
    reading = fred.fetch_live("DGS10", fetched_at="t")[0]
    assert reading.payload.keys() == {"series_id", "value"}
    assert reading.payload["series_id"] == "DGS10"
    assert isinstance(reading.payload["value"], float)


@skip_unless_live
def test_fund_holdings_live_smoke() -> None:
    # AkShare queries holdings per calendar year, derived from the run stamp — pass a real one.
    reading = fund_holdings.fetch_live(_PROBE, fetched_at="2026-06-05T22:00:00Z")[0]
    assert reading.payload.keys() == {"fund", "holding", "weight"}
    assert reading.payload["fund"] == _PROBE
    assert 0.0 <= reading.payload["weight"] <= 1.0


@skip_unless_live
def test_fund_universe_live_smoke() -> None:
    readings = fund_universe.fetch_live(as_of="2026-06-05", fetched_at="t")
    assert readings and "on_exchange" in readings[0].payload  # all funds, ETFs marked on-exchange
    # the launch-at-peak guardrail needs real launch dates: on-exchange funds carry one live
    assert any(r.payload["on_exchange"] and r.payload["inception"] for r in readings)


@skip_unless_live
def test_etf_scale_live_smoke() -> None:
    reading = etf_scale.fetch_live(fetched_at="t")[0]
    assert reading.payload.keys() == {"exchange", "aum", "shares"}
    assert reading.payload["exchange"] in {"sse", "szse"}
    assert reading.payload["aum"] > 0 and reading.payload["shares"] > 0


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
def test_trading_activity_live_smoke() -> None:
    reading = trading_activity.fetch_live(_PROBE, fetched_at="t")[0]
    assert reading.payload.keys() == {"turnover", "amount"}
    assert reading.payload["turnover"] >= 0 and reading.payload["amount"] >= 0


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
