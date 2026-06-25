"""Daily per-fund trading activity — turnover and traded value, the crowding/illiquidity surface.

Reads ``{code, as_of, turnover, amount}`` rows — one per fund per session, keyed by code and
stamped with the bar's own trading date. ``turnover`` is the daily turnover ratio (换手率, the
crowding signal); ``amount`` is the daily traded value (成交额, the Amihud-illiquidity input). Both
are read point-in-time against a fund's own history by the factor battery.

Live, the current bar comes from the shared whole-market spot board (:func:`spot_reading`) and
per-fund EastMoney K-line history (:func:`from_kline`) is pulled only to seed or backfill — the
spot-vs-deep load-shape decision lives in the store-aware
:class:`~factor_scope.ingest.feed.LiveFeed`, which rides the same browser-impersonating K-line
client the NAV leg does (one fetch feeds both legs). This module is the pure mapping; never called
in CI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from factor_scope.ingest.base import spot_date
from factor_scope.store import Reading

SERIES = "trading_activity"


def from_kline(
    code: str, bars: Iterable[Mapping[str, Any]], *, fetched_at: str, floor: str | None = None
) -> list[Reading]:
    """Map the K-line client's domain bars (``{date, turnover, amount}``) to Readings, oldest-first.

    ``floor`` is the incremental-fetch watermark: only bars strictly newer than it become rows, so a
    re-pull that overlaps the stored history writes nothing already held. One K-line fetch feeds
    both this leg (``turnover`` / ``amount``) and the price NAV leg (``close``) — see the feed.
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
        if floor is None or str(bar["date"]) > floor
    ]


def spot_reading(
    board: Mapping[str, Any], code: str, *, fetched_at: str, settled: bool, floor: str | None
) -> list[Reading]:
    """One fund's current-session turnover + traded value from the shared whole-market spot board.

    Steady state reads the current bar off the cheap batch board rather than a per-code ``push2his``
    pull. A bar whose session date is the closed trading session (``settled``) records as settled
    history and advances the watermark; otherwise it is tagged ``provisional`` so the floor
    (:func:`markets.ashare._series_watermarks`) skips it and a later K-line pull backfills the
    sessions it stood in for. No rows for a code absent from the board (e.g. delisted).
    """

    row = board.get(code)
    if row is None:
        return []
    bar = {"date": spot_date(row["数据日期"]), "turnover": row["换手率"], "amount": row["成交额"]}
    readings = from_kline(code, [bar], fetched_at=fetched_at, floor=floor)
    if settled:
        return readings
    return [
        reading.model_copy(update={"payload": {**reading.payload, "provisional": True}})
        for reading in readings
    ]
