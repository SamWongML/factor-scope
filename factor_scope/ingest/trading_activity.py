"""Daily per-fund trading activity — turnover and traded value, the crowding/illiquidity surface.

Reads ``{code, as_of, turnover, amount}`` rows — one per fund per session, keyed by code and
stamped with the bar's own trading date. ``turnover`` is the daily turnover ratio (换手率, the
crowding signal); ``amount`` is the daily traded value (成交额, the Amihud-illiquidity input). Both
are read point-in-time against a fund's own history by the factor battery.

Live pulls EastMoney's daily K-line through the browser-impersonating
:mod:`~factor_scope.ingest.eastmoney` client (the same leg the NAV adapter rides) — never called in
CI.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from factor_scope.ingest import eastmoney
from factor_scope.ingest.base import EASTMONEY_KLINE, host_breaker
from factor_scope.ingest.prices import _em_start
from factor_scope.store import Reading

logger = logging.getLogger(__name__)

SERIES = "trading_activity"


def _from_bars(
    code: str, bars: Iterable[Mapping[str, Any]], *, fetched_at: str, since: str | None = None
) -> list[Reading]:
    """Map the K-line client's domain bars (``{date, turnover, amount}``) to Readings — live's core.

    ``since`` is the incremental-fetch watermark: only bars strictly newer than it become rows, so a
    re-pull that overlaps the stored history writes nothing already held.
    """

    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(bar["date"]),
            fetched_at=fetched_at,
            payload={"turnover": float(bar["turnover"]), "amount": float(bar["amount"])},
        )
        for bar in bars
        if since is None or str(bar["date"]) > since
    ]


def fetch_live(
    board: Mapping[str, Any],
    code: str,
    *,
    fetched_at: str,
    since: str | None = None,
    impersonate: str = "chrome",
) -> list[Reading]:
    """Pull a fund's daily turnover + traded value via EastMoney's K-line. Requires `live`.

    ``since`` is the latest ``as_of`` already stored for this code: when set, the request starts the
    day after it (the K-line ``beg``) so the multi-year history is fetched once and each later night
    pulls only the new sessions — turning the nightly re-pull from quadratic to linear; a cold pull
    seeds the same bounded window the NAV leg uses (:func:`prices._em_start`), anchored on the run
    date, rather than the full history that would burst the shared host on the first night.

    EastMoney's per-fund history is primary, fetched through the browser-impersonating
    :mod:`eastmoney` K-line client that defeats the ``push2his`` reset; when that host refuses the
    request, the current session's turnover + traded value come from the shared whole-market spot
    ``board`` (the single per-run snapshot) instead, so a block on one host degrades the crowding
    surface to today's bar rather than dropping the fund entirely.
    """

    if host_breaker.is_open(EASTMONEY_KLINE):  # host known-blocked → skip it, use the spot board
        return _spot_bar(board, code, fetched_at=fetched_at, since=since)
    beg = _em_start(fetched_at, since)
    try:
        bars = eastmoney.kline(code, beg=beg, impersonate=impersonate)
        host_breaker.record_success(EASTMONEY_KLINE)
    except Exception as exc:
        host_breaker.record_failure(EASTMONEY_KLINE)
        logger.warning(
            "trading_activity: EastMoney history refused %s (%s); falling back to the spot board",
            code,
            exc,
        )
        return _spot_bar(board, code, fetched_at=fetched_at, since=since)
    return _from_bars(code, bars, fetched_at=fetched_at, since=since)


def _spot_bar(
    board: Mapping[str, Any], code: str, *, fetched_at: str, since: str | None
) -> list[Reading]:
    """One fund's current-session bar from the shared spot ``board`` — the fallback when the history
    host is unreachable. Returns no rows for a code absent from the board (e.g. delisted)."""

    row = board.get(code)
    if row is None:
        return []
    return _spot_reading(code, row, fetched_at=fetched_at, since=since)


def _spot_reading(
    code: str, row: Mapping[str, Any], *, fetched_at: str, since: str | None
) -> list[Reading]:
    """Map one spot-board row to a Reading — same shape as history, tagged ``provisional``.

    The tag marks this as the current-session estimate, not a settled history bar, so the floor
    (:func:`markets.ashare._series_watermarks`) skips it and a recovered history pull backfills the
    sessions the outage missed instead of starting past them.
    """

    bar = {
        "date": _spot_date(row["数据日期"]),
        "turnover": row["换手率"],
        "amount": row["成交额"],
    }
    return [
        reading.model_copy(update={"payload": {**reading.payload, "provisional": True}})
        for reading in _from_bars(code, [bar], fetched_at=fetched_at, since=since)
    ]


def _spot_date(value: Any) -> str:
    """The session date from a spot row as ISO ``YYYY-MM-DD`` (it arrives as a pandas Timestamp)."""

    if hasattr(value, "strftime"):
        return str(value.strftime("%Y-%m-%d"))
    return str(value)
