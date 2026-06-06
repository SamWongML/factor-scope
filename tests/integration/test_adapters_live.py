"""Live-backend smoke tests for the L1 adapters.

These hit the network and are **skipped unless ``FACTOR_SCOPE_LIVE=1``**, so CI never calls a live
source. They are the documented way to verify each adapter's opt-in ``--live`` path by hand.
"""

import os

import pytest

from factor_scope.ingest import edgar, fred, fund_holdings, prices

pytestmark = pytest.mark.integration

_LIVE = os.environ.get("FACTOR_SCOPE_LIVE") == "1"
skip_unless_live = pytest.mark.skipif(not _LIVE, reason="set FACTOR_SCOPE_LIVE=1 to run live")


@skip_unless_live
def test_prices_live_smoke() -> None:
    readings = prices.fetch_live("561010", fetched_at="t")
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
def test_edgar_13f_live_smoke() -> None:
    readings = edgar.fetch_live("0001067983", fetched_at="t")  # Berkshire Hathaway 13F-HR
    assert readings and "shares" in readings[0].payload


@skip_unless_live
def test_edgar_nport_live_smoke() -> None:
    readings = edgar.fetch_live("0000036405", form="NPORT-P", fetched_at="t")  # Vanguard 500 N-PORT
    assert readings and "shares" in readings[0].payload
