"""The trading-activity ingest adapter — daily turnover + traded value, the crowding surface.

This pins the live EastMoney K-line backend: the same browser-impersonating client the NAV leg
rides, returning domain bars ``{date, turnover, amount}``. Each row is keyed by code and stamped
with the bar's own trading date (not the run date), carrying ``turnover`` (换手率, the crowding
signal) and ``amount`` (成交额, the Amihud-illiquidity input). The offline replay is covered in
``tests/unit/test_feed.py``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pytest

from factor_scope.ingest import eastmoney, prices, trading_activity
from factor_scope.ingest.base import EASTMONEY_KLINE, _HostBreaker

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"


def fake_kline(
    monkeypatch: Any,
    *,
    bars: list[dict[str, Any]] | None = None,
    error: Exception | None = None,
    captured: dict[str, Any] | None = None,
) -> None:
    """Mock the EastMoney K-line client the activity history leg now fetches through.

    Returns domain bars ``{date, close, turnover, amount}`` (the client's contract), raises
    ``error`` to simulate a ``push2his`` reset, or records the ``beg`` it saw into ``captured``.
    """

    def _kline(code: str, *, beg: str, impersonate: str = "chrome") -> list[dict[str, Any]]:
        if captured is not None:
            captured.update({"code": code, "beg": beg, "impersonate": impersonate})
        if error is not None:
            raise error
        return bars or []

    monkeypatch.setattr(eastmoney, "kline", _kline)


def _bar(as_of: str, *, turnover: float, amount: float) -> dict[str, Any]:
    """A domain bar as the EastMoney client returns it; the activity leg reads turnover + amount."""

    return {"date": as_of, "close": 0.0, "turnover": turnover, "amount": amount}


def test_trading_activity_maps_the_kline_bar_columns() -> None:
    # the client's domain bar ({date, turnover, amount}) maps to the same Reading shape as the
    # fixture, pinned offline so the column mapping is covered without the network.
    bars = [_bar("2026-05-30", turnover=4.25, amount=3.60)]
    reading = trading_activity._from_bars("561010", bars, fetched_at=FETCHED_AT)[0]
    assert reading.key == "561010"
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"turnover": 4.25, "amount": 3.60}


def test_trading_activity_from_bars_keeps_only_bars_past_the_watermark() -> None:
    # the incremental re-pull drops bars at or before the stored watermark (the K-line beg is
    # inclusive, so the boundary bar can return) — only strictly-newer sessions become rows.
    bars = [
        _bar("2026-05-29", turnover=1.0, amount=1.0),
        _bar("2026-05-30", turnover=2.0, amount=2.0),
    ]
    kept = trading_activity._from_bars("561010", bars, fetched_at=FETCHED_AT, since="2026-05-29")
    assert [r.as_of for r in kept] == ["2026-05-30"]


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


def _board(code: str = "561010", *, turnover: float = 5.15, amount: float = 1.0) -> dict[str, Any]:
    """A one-row shared spot board (keyed by code, like ``etf_scale.fetch_spot_board``) for the
    fallback path — the live feed pulls this once per run and hands it to the activity leg."""

    return {
        code: {"代码": code, "数据日期": date(2026, 6, 16), "换手率": turnover, "成交额": amount}
    }


def test_trading_activity_fetch_live_prefers_history_when_reachable(monkeypatch) -> None:
    fake_kline(monkeypatch, bars=[_bar("2026-05-30", turnover=4.25, amount=3.60)])
    # an empty board would yield no fallback rows, so a non-empty reading proves history answered
    reading = trading_activity.fetch_live({}, "561010", fetched_at=FETCHED_AT)[0]
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"turnover": 4.25, "amount": 3.60}


def test_trading_activity_fetch_live_starts_one_day_past_the_watermark(monkeypatch) -> None:
    # With a stored watermark the history request starts the day after it (YYYYMMDD beg), so the
    # nightly re-pull asks EastMoney only for sessions newer than the store already holds.
    captured: dict[str, object] = {}
    fake_kline(
        monkeypatch, bars=[_bar("2026-05-30", turnover=4.25, amount=3.60)], captured=captured
    )
    trading_activity.fetch_live({}, "561010", fetched_at=FETCHED_AT, since="2026-05-29")
    assert captured["beg"] == "20260530"


def test_trading_activity_cold_pull_seeds_the_bounded_window_not_full_history(monkeypatch) -> None:
    # A cold pull (no watermark) seeds the same bounded window the NAV leg uses off the same K-line
    # client, anchored on the run date — not the full-history epoch, which would burst the
    # rate-limited push2his host with every fund's entire history on the first night.
    captured: dict[str, object] = {}
    fake_kline(
        monkeypatch, bars=[_bar("2026-05-30", turnover=4.25, amount=3.60)], captured=captured
    )
    trading_activity.fetch_live({}, "561010", fetched_at=FETCHED_AT)
    assert captured["beg"] != "19700101"  # not the full-history epoch
    assert captured["beg"] == prices._em_start(FETCHED_AT, None)  # the NAV leg's seed window


def test_trading_activity_fetch_live_threads_the_impersonation_profile(monkeypatch) -> None:
    # The activity history leg rides the same browser-impersonating K-line client (same host, same
    # breaker) as the NAV leg, so the config-driven fingerprint must reach it too — else a bumped
    # profile fixes NAV but leaves activity hitting the same host with the stale one.
    captured: dict[str, object] = {}
    fake_kline(
        monkeypatch, bars=[_bar("2026-05-30", turnover=4.25, amount=3.60)], captured=captured
    )
    trading_activity.fetch_live({}, "561010", fetched_at=FETCHED_AT, impersonate="chrome131")
    assert captured["impersonate"] == "chrome131"  # the configured profile reached the client


def test_trading_activity_falls_back_to_spot_when_history_refused(monkeypatch) -> None:
    fake_kline(monkeypatch, error=ConnectionError("history host closed the connection"))
    board = {
        "515880": {"代码": "515880", "数据日期": date(2026, 6, 16), "换手率": 1.0, "成交额": 2.0},
        "561010": {"代码": "561010", "数据日期": date(2026, 6, 16), "换手率": 5.15,
                   "成交额": 12095104.0},
    }
    reading = trading_activity.fetch_live(board, "561010", fetched_at=FETCHED_AT)[0]
    assert reading.as_of == "2026-06-16"  # the spot session, found by code on the shared board
    assert reading.payload == {"turnover": 5.15, "amount": 12095104.0, "provisional": True}


def test_spot_fallback_yields_no_reading_for_a_fund_absent_from_the_board(monkeypatch) -> None:
    # A delisted/absent code simply isn't on the spot board → the crowding surface degrades to no
    # reading (the factor falls to invalid), never a crash.
    fake_kline(monkeypatch, error=ConnectionError("history host closed the connection"))
    assert trading_activity.fetch_live(_board("515880"), "561010", fetched_at=FETCHED_AT) == []


def test_trading_activity_skips_eastmoney_while_the_breaker_is_open(monkeypatch) -> None:
    # The shared host breaker spans the activity leg too: once tripped, go straight to the spot
    # board for the rest of the run rather than re-hitting a blocking IP per fund.
    breaker = _HostBreaker(threshold=1)
    breaker.record_failure(EASTMONEY_KLINE)  # tripped open
    monkeypatch.setattr(trading_activity, "host_breaker", breaker)

    fake_kline(
        monkeypatch,
        error=AssertionError("EastMoney must be skipped while the breaker is open"),
    )
    reading = trading_activity.fetch_live(_board(), "561010", fetched_at=FETCHED_AT)[0]
    assert reading.payload["provisional"] is True  # served by the spot board, the host untouched


def test_trading_activity_records_a_breaker_failure_when_eastmoney_refuses(monkeypatch) -> None:
    breaker = _HostBreaker(threshold=5)
    monkeypatch.setattr(trading_activity, "host_breaker", breaker)

    fake_kline(monkeypatch, error=ConnectionError("history host closed the connection"))
    trading_activity.fetch_live(_board(), "561010", fetched_at=FETCHED_AT)
    assert breaker.failures(EASTMONEY_KLINE) == 1  # the refusal counts toward tripping the breaker


def test_trading_activity_logs_loudly_when_it_falls_back_to_spot(monkeypatch, caplog) -> None:
    # A silent history→spot swap would hide a persistently-blocked primary; the fallback must log so
    # the degradation is visible even though the spot board covers the current session.
    fake_kline(monkeypatch, error=ConnectionError("history host closed the connection"))
    with caplog.at_level(logging.WARNING):
        trading_activity.fetch_live(_board(), "561010", fetched_at=FETCHED_AT)
    assert any(r.levelno == logging.WARNING and "561010" in r.getMessage() for r in caplog.records)
