"""Live-backend smoke tests for the L1 adapters.

These hit the network and are **skipped unless ``FACTOR_SCOPE_LIVE=1``**, so CI never calls a live
source. They are the documented way to verify each adapter's opt-in ``--live`` path by hand.
"""

import os

import pytest

from factor_scope.ingest import fred, prices

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
