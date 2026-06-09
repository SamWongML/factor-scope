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
