"""The trading-activity ingest adapter — daily turnover + traded value, the crowding surface.

This pins the offline backend: each row is keyed by code and stamped with the bar's own trading
date (not the run date), carrying ``turnover`` (换手率, the crowding signal) and ``amount`` (成交额,
the Amihud-illiquidity input). A malformed header or non-numeric value is a hard parse error.
"""

from __future__ import annotations

import pytest

from factor_scope.ingest import trading_activity
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"

_ACTIVITY = (
    "code,as_of,turnover,amount\n"
    "561010,2026-05-29,3.10,2.80\n"
    "515880,2026-05-30,2.05,1.55\n"
)


def test_trading_activity_carries_turnover_and_amount_stamped_with_the_bar_date() -> None:
    readings = trading_activity.parse(_ACTIVITY, fetched_at=FETCHED_AT)
    first = readings[0]
    assert first.series == trading_activity.SERIES
    assert first.key == "561010"
    assert first.as_of == "2026-05-29"  # the bar's own trading date, not the run date
    assert first.payload["turnover"] == 3.10
    assert first.payload["amount"] == 2.80


def test_trading_activity_rejects_a_malformed_header() -> None:
    with pytest.raises(IngestError):
        trading_activity.parse("code,as_of,turnover\n561010,2026-05-29,3.10\n", fetched_at="t")


def test_trading_activity_rejects_a_non_numeric_value() -> None:
    with pytest.raises(IngestError):
        trading_activity.parse(
            "code,as_of,turnover,amount\n561010,2026-05-29,n/a,2.80\n", fetched_at=FETCHED_AT
        )


def test_trading_activity_maps_the_akshare_bar_columns() -> None:
    # the live ETF daily bar (日期 / 换手率 / 成交额) maps to the same Reading shape as the fixture,
    # pinned offline so the column mapping is covered without the network.
    bars = [{"日期": "2026-05-30", "换手率": "4.25", "成交额": "3.60"}]
    reading = trading_activity.from_bars("561010", bars, fetched_at=FETCHED_AT)[0]
    assert reading.key == "561010"
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"turnover": 4.25, "amount": 3.60}
