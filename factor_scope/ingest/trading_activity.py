"""Daily per-fund trading activity — turnover and traded value, the crowding/illiquidity surface.

`trading_activity.csv → {code, as_of, turnover, amount}`. One row per fund per session, keyed by
code and stamped with the bar's own trading date. ``turnover`` is the daily turnover ratio (换手率,
the crowding signal); ``amount`` is the daily traded value (成交额, the Amihud-illiquidity input).
Both are read point-in-time against a fund's own history by the factor battery.

Live pulls AkShare's on-exchange ETF daily bar (``fund_etf_hist_em``) — never called in CI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "trading_activity"
FIXTURE = "trading_activity.csv"
_REQUIRED = ("code", "as_of", "turnover", "amount")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        readings.append(
            Reading(
                series=SERIES,
                key=required_str(row, "code", line_no, SERIES),
                as_of=required_str(row, "as_of", line_no, SERIES),
                fetched_at=fetched_at,
                payload={
                    "turnover": as_float(row, "turnover", line_no, SERIES),
                    "amount": as_float(row, "amount", line_no, SERIES),
                },
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def _from_bars(
    code: str, bars: Iterable[Mapping[str, Any]], *, fetched_at: str, since: str | None = None
) -> list[Reading]:
    """Map AkShare's ETF daily bars (日期 / 换手率 / 成交额) to Readings — the pure core of live.

    ``since`` is the incremental-fetch watermark: only bars strictly newer than it become rows, so a
    re-pull that overlaps the stored history writes nothing already held.
    """

    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(bar["日期"]),
            fetched_at=fetched_at,
            payload={"turnover": float(bar["换手率"]), "amount": float(bar["成交额"])},
        )
        for bar in bars
        if since is None or str(bar["日期"]) > since
    ]


def fetch_live(
    code: str, *, fetched_at: str, since: str | None = None
) -> list[Reading]:  # pragma: no cover - live path
    """Pull a fund's daily turnover + traded value via AkShare. Requires `live` + network.

    ``since`` is the latest ``as_of`` already stored for this code: when set, the request starts the
    day after it (AkShare's ``start_date``) so the multi-year history is fetched once and each later
    night pulls only the new sessions — turning the nightly re-pull from quadratic to linear.
    """

    import akshare as ak

    kwargs = {"start_date": _start_date(since)} if since is not None else {}
    frame = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="", **kwargs)
    bars = (bar for _, bar in frame.iterrows())
    return _from_bars(code, bars, fetched_at=fetched_at, since=since)


def _start_date(since: str) -> str:
    """The AkShare ``start_date`` (``YYYYMMDD``) one day past the watermark — only newer bars."""

    return (date.fromisoformat(since) + timedelta(days=1)).strftime("%Y%m%d")
