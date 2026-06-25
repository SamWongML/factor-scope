"""The trading-activity ingest adapter — the pure mapping for the crowding/illiquidity surface.

This pins the two mappers the store-aware feed drives: :func:`from_kline` (the deep K-line domain
bars ``{date, turnover, amount}`` → Readings, one fetch shared with the NAV leg) and
:func:`spot_reading` (one whole-market spot-board row → the current bar, settled when it is the
closed session, else ``provisional``). The spot-vs-deep routing, the breaker, and the Sina/spot
fallback live in ``tests/unit/test_feed.py`` (the LiveFeed seam); the offline replay in
``tests/unit/test_feed.py`` too.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from factor_scope.ingest import trading_activity

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"


def _bar(as_of: str, *, turnover: float, amount: float) -> dict[str, Any]:
    """A domain bar as the EastMoney client returns it; the activity leg reads turnover + amount."""

    return {"date": as_of, "close": 0.0, "turnover": turnover, "amount": amount}


def test_from_kline_maps_the_bar_columns() -> None:
    # the client's domain bar ({date, turnover, amount}) maps to the same Reading shape as the
    # fixture, pinned offline so the column mapping is covered without the network.
    reading = trading_activity.from_kline(
        "561010", [_bar("2026-05-30", turnover=4.25, amount=3.60)], fetched_at=FETCHED_AT
    )[0]
    assert reading.series == "trading_activity"
    assert reading.key == "561010"
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"turnover": 4.25, "amount": 3.60}


def test_from_kline_keeps_only_bars_past_the_floor() -> None:
    # the incremental re-pull drops bars at or before the stored watermark (the K-line beg is
    # inclusive, so the boundary bar can return) — only strictly-newer sessions become rows.
    bars = [
        _bar("2026-05-29", turnover=1.0, amount=1.0),
        _bar("2026-05-30", turnover=2.0, amount=2.0),
    ]
    kept = trading_activity.from_kline("561010", bars, fetched_at=FETCHED_AT, floor="2026-05-29")
    assert [r.as_of for r in kept] == ["2026-05-30"]


def _row(*, turnover: float = 5.15, amount: float = 12095104.0) -> dict[str, Any]:
    """One whole-market spot-board row (Chinese keys, date as a pandas-style Timestamp)."""

    return {"数据日期": date(2026, 6, 16), "换手率": turnover, "成交额": amount}


def test_spot_reading_maps_the_session_turnover_and_traded_value() -> None:
    # the spot board carries the date as a pandas Timestamp; it normalises to the same ISO as_of
    # and the same Reading shape as the history path, so the current-bar leg is a drop-in.
    rows = trading_activity.spot_reading(
        {"561010": _row()}, "561010", fetched_at=FETCHED_AT, settled=True, floor=None
    )
    assert rows[0].as_of == "2026-06-16"
    assert rows[0].payload == {"turnover": 5.15, "amount": 12095104.0}  # settled → no provisional


def test_spot_reading_is_provisional_when_not_settled() -> None:
    # An unsettled (intraday/holiday/outage) bar is the current session only, not settled history;
    # the provenance marker lets the incremental floor skip it so a later K-line pull backfills.
    rows = trading_activity.spot_reading(
        {"561010": _row()}, "561010", fetched_at=FETCHED_AT, settled=False, floor=None
    )
    assert rows[0].payload == {"turnover": 5.15, "amount": 12095104.0, "provisional": True}


def test_spot_reading_respects_the_floor() -> None:
    # today's bar already stored (floor == its date) is not re-appended — idempotent re-runs.
    rows = trading_activity.spot_reading(
        {"561010": _row()}, "561010", fetched_at=FETCHED_AT, settled=True, floor="2026-06-16"
    )
    assert rows == []


def test_spot_reading_yields_no_reading_for_a_fund_absent_from_the_board() -> None:
    # A delisted/absent code simply isn't on the spot board → the crowding surface degrades to no
    # reading (the factor falls to invalid), never a crash.
    assert (
        trading_activity.spot_reading(
            {"515880": _row()}, "561010", fetched_at=FETCHED_AT, settled=True, floor=None
        )
        == []
    )
