"""Live-backend smoke tests for the ingestion adapters.

These hit the network and are **skipped unless ``FACTOR_SCOPE_LIVE=1``**, so CI never calls a live
source. They are the documented way to verify each adapter's live path by hand.
"""

import os

import pytest

from factor_scope.ingest import (
    baostock,
    edgar,
    etf_scale,
    fred,
    fund_holdings,
    fund_universe,
    mootdx,
    prices,
    trading_activity,
)

pytestmark = pytest.mark.integration

_LIVE = os.environ.get("FACTOR_SCOPE_LIVE") == "1"
skip_unless_live = pytest.mark.skipif(not _LIVE, reason="set FACTOR_SCOPE_LIVE=1 to run live")


@skip_unless_live
def test_prices_live_smoke() -> None:
    readings = prices.fetch_live("561010", fetched_at="t")
    assert readings and readings[0].payload["nav"] > 0


@skip_unless_live
def test_baostock_live_smoke() -> None:
    readings = baostock.fetch_live("561010", fetched_at="t")  # the Baostock cross-validation leg
    assert readings and readings[0].payload["nav"] > 0


@skip_unless_live
def test_mootdx_live_smoke() -> None:
    readings = mootdx.fetch_live("561010", fetched_at="t")  # the third (TDX) cross-validation leg
    assert readings and readings[0].payload["nav"] > 0


@skip_unless_live
def test_fred_live_smoke() -> None:
    readings = fred.fetch_live("DGS10", fetched_at="t")
    assert readings and "value" in readings[0].payload


@skip_unless_live
def test_fund_holdings_live_smoke() -> None:
    readings = fund_holdings.fetch_live("561010", fetched_at="t")
    assert readings and "weight" in readings[0].payload


@skip_unless_live
def test_fund_universe_live_smoke() -> None:
    readings = fund_universe.fetch_live(as_of="2026-06-05", fetched_at="t")
    assert readings and "on_exchange" in readings[0].payload  # all funds, ETFs marked on-exchange


@skip_unless_live
def test_etf_scale_live_smoke() -> None:
    readings = etf_scale.fetch_live(fetched_at="t")
    assert readings and readings[0].payload["aum"] > 0


@skip_unless_live
def test_trading_activity_live_smoke() -> None:
    readings = trading_activity.fetch_live("561010", fetched_at="t")
    assert readings and readings[0].payload["turnover"] >= 0 and readings[0].payload["amount"] >= 0


@skip_unless_live
def test_edgar_13f_live_smoke() -> None:
    readings = edgar.fetch_live("0001067983", fetched_at="t")  # Berkshire Hathaway 13F-HR
    assert readings and "shares" in readings[0].payload


@skip_unless_live
def test_edgar_nport_live_smoke() -> None:
    readings = edgar.fetch_live("0000036405", form="NPORT-P", fetched_at="t")  # Vanguard 500 N-PORT
    assert readings and 0.0 <= readings[0].payload["weight"] <= 1.0
