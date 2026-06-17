"""The trading-activity ingest adapter — daily turnover + traded value, the crowding surface.

This pins the offline backend: each row is keyed by code and stamped with the bar's own trading
date (not the run date), carrying ``turnover`` (换手率, the crowding signal) and ``amount`` (成交额,
the Amihud-illiquidity input). A malformed header or non-numeric value is a hard parse error.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from factor_scope.ingest import trading_activity
from factor_scope.ingest.base import IngestError
from tests.unit._akshare_fakes import FakeFrame, install_fake_akshare

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
    reading = trading_activity._from_bars("561010", bars, fetched_at=FETCHED_AT)[0]
    assert reading.key == "561010"
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"turnover": 4.25, "amount": 3.60}


def test_trading_activity_from_bars_keeps_only_bars_past_the_watermark() -> None:
    # the incremental re-pull drops bars at or before the stored watermark (AkShare's start_date is
    # inclusive, so the boundary bar can return) — only strictly-newer sessions become rows.
    bars = [
        {"日期": "2026-05-29", "换手率": "1.0", "成交额": "1.0"},
        {"日期": "2026-05-30", "换手率": "2.0", "成交额": "2.0"},
    ]
    kept = trading_activity._from_bars("561010", bars, fetched_at=FETCHED_AT, since="2026-05-29")
    assert [r.as_of for r in kept] == ["2026-05-30"]


def test_trading_activity_start_date_is_the_day_after_the_watermark() -> None:
    # the AkShare start_date is the day past the watermark (YYYYMMDD), so the request never re-pulls
    # the stored bar — and it rolls the year correctly at a year boundary.
    assert trading_activity._start_date("2026-06-30") == "20260701"
    assert trading_activity._start_date("2026-12-31") == "20270101"


def test_spot_reading_maps_the_session_turnover_and_traded_value() -> None:
    # the spot board carries the date as a pandas Timestamp; it normalises to the same ISO as_of
    # and the same Reading shape as the history path, so the fallback is a drop-in.
    rows = trading_activity._spot_reading(
        "561010",
        {"数据日期": date(2026, 6, 16), "换手率": 5.15, "成交额": 12095104.0},
        fetched_at=FETCHED_AT,
        since=None,
    )
    assert rows[0].as_of == "2026-06-16"
    assert rows[0].payload == {"turnover": 5.15, "amount": 12095104.0, "provisional": True}


def test_spot_reading_respects_the_watermark() -> None:
    # today's bar already stored (since == its date) is not re-appended — idempotent re-runs
    rows = trading_activity._spot_reading(
        "561010",
        {"数据日期": date(2026, 6, 16), "换手率": 5.15, "成交额": 1.0},
        fetched_at=FETCHED_AT,
        since="2026-06-16",
    )
    assert rows == []


def test_spot_reading_is_marked_provisional() -> None:
    # A spot bar is the current session only, not a settled history bar; the provenance marker lets
    # the incremental floor skip it, so a later history pull backfills the sessions it missed.
    rows = trading_activity._spot_reading(
        "561010",
        {"数据日期": date(2026, 6, 16), "换手率": 5.15, "成交额": 12095104.0},
        fetched_at=FETCHED_AT,
        since=None,
    )
    assert rows[0].payload["provisional"] is True


def test_trading_activity_fetch_live_prefers_history_when_reachable(monkeypatch) -> None:
    def em(**kwargs: object) -> FakeFrame:
        return FakeFrame([{"日期": "2026-05-30", "换手率": "4.25", "成交额": "3.60"}])

    def spot() -> FakeFrame:
        raise AssertionError("the spot board must not be called when history answers")

    install_fake_akshare(monkeypatch, fund_etf_hist_em=em, fund_etf_spot_em=spot)
    reading = trading_activity.fetch_live("561010", fetched_at=FETCHED_AT)[0]
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"turnover": 4.25, "amount": 3.60}


def test_trading_activity_fetch_live_starts_one_day_past_the_watermark(monkeypatch) -> None:
    # With a stored watermark the history request starts the day after it (YYYYMMDD), so the nightly
    # re-pull asks EastMoney only for sessions newer than the store already holds.
    seen: dict[str, object] = {}

    def em(**kwargs: object) -> FakeFrame:
        seen.update(kwargs)
        return FakeFrame([{"日期": "2026-05-30", "换手率": "4.25", "成交额": "3.60"}])

    install_fake_akshare(monkeypatch, fund_etf_hist_em=em)
    trading_activity.fetch_live("561010", fetched_at=FETCHED_AT, since="2026-05-29")
    assert seen["start_date"] == "20260530"


def test_trading_activity_falls_back_to_spot_when_history_refused(monkeypatch) -> None:
    trading_activity._spot_snapshot.cache_clear()  # the spot board is memoised across the run

    def em(**kwargs: object) -> FakeFrame:
        raise ConnectionError("history host closed the connection")

    def spot() -> FakeFrame:
        day = date(2026, 6, 16)
        return FakeFrame(
            [
                {"代码": "515880", "数据日期": day, "换手率": 1.0, "成交额": 2.0},
                {"代码": "561010", "数据日期": day, "换手率": 5.15, "成交额": 12095104.0},
            ]
        )

    install_fake_akshare(monkeypatch, fund_etf_hist_em=em, fund_etf_spot_em=spot)
    reading = trading_activity.fetch_live("561010", fetched_at=FETCHED_AT)[0]
    assert reading.as_of == "2026-06-16"  # the spot session, found by code on the board
    assert reading.payload == {"turnover": 5.15, "amount": 12095104.0, "provisional": True}
    trading_activity._spot_snapshot.cache_clear()


def test_spot_fallback_yields_no_reading_for_a_fund_absent_from_the_board(monkeypatch) -> None:
    # A delisted/absent code simply isn't on the spot board → the crowding surface degrades to no
    # reading (the factor falls to invalid), never a crash.
    trading_activity._spot_snapshot.cache_clear()

    def em(**kwargs: object) -> FakeFrame:
        raise ConnectionError("history host closed the connection")

    def spot() -> FakeFrame:
        return FakeFrame(
            [{"代码": "515880", "数据日期": date(2026, 6, 16), "换手率": 1.0, "成交额": 2.0}]
        )

    install_fake_akshare(monkeypatch, fund_etf_hist_em=em, fund_etf_spot_em=spot)
    assert trading_activity.fetch_live("561010", fetched_at=FETCHED_AT) == []
    trading_activity._spot_snapshot.cache_clear()


def test_trading_activity_logs_loudly_when_it_falls_back_to_spot(monkeypatch, caplog) -> None:
    # A silent history→spot swap would hide a persistently-blocked primary; the fallback must log so
    # the degradation is visible even though the spot board covers the current session.
    trading_activity._spot_snapshot.cache_clear()

    def em(**kwargs: object) -> FakeFrame:
        raise ConnectionError("history host closed the connection")

    def spot() -> FakeFrame:
        rows = [{"代码": "561010", "数据日期": date(2026, 6, 16), "换手率": 5.15, "成交额": 1.0}]
        return FakeFrame(rows)

    install_fake_akshare(monkeypatch, fund_etf_hist_em=em, fund_etf_spot_em=spot)
    with caplog.at_level(logging.WARNING):
        trading_activity.fetch_live("561010", fetched_at=FETCHED_AT)
    assert any(r.levelno == logging.WARNING and "561010" in r.getMessage() for r in caplog.records)
    trading_activity._spot_snapshot.cache_clear()
