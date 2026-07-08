"""Quarterly fund/ETF holdings. Reads ``{fund, as_of, holding, weight}`` rows.

These rows become the connection-graph edges (the exact look-through). Each ``(fund,
holding)`` pair is its own point-in-time key so a quarter's disclosure never overwrites a prior one.
Live is AkShare's ``fund_portfolio_hold_em`` — never called in CI.
"""

from __future__ import annotations

import re
from typing import Any

from factor_scope.ingest.base import IngestError
from factor_scope.store import Reading

SERIES = "fund_holdings"

# A quarter's report-period label → its quarter-END date. The graph windows and the point-in-time
# store key on ISO ``as_of``, so the disclosure is stamped the quarter it closes, not a text label.
_QUARTER_END = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}
_QUARTER_LABEL = re.compile(r"(?P<year>\d{4})年(?P<quarter>[1-4])季度")


def fetch_live(
    fund: str, *, fetched_at: str, since: str | None = None
) -> list[Reading]:  # pragma: no cover - live path
    """Pull a fund's disclosed stock holdings via AkShare. Requires `live` + network.

    AkShare's ``fund_portfolio_hold_em`` is queried per calendar year. ``since`` is the latest
    quarter-end already stored for this fund: the request spans only that disclosure year onward
    (the run year when nothing is stored — derived from the run stamp, never a hard-coded lookback);
    any quarter at or before the watermark is dropped, so a re-pull adds only newly disclosed ones.
    """

    import akshare as ak

    readings: list[Reading] = []
    for year in range(_first_year(since, fetched_at), int(fetched_at[:4]) + 1):
        frame = ak.fund_portfolio_hold_em(symbol=fund, date=str(year))
        readings += from_portfolio(
            fund, [row for _, row in frame.iterrows()], fetched_at=fetched_at, floor=since
        )
    return readings


def from_portfolio(
    fund: str, rows: list[Any], *, fetched_at: str, floor: str | None = None
) -> list[Reading]:
    """Map AkShare ``fund_portfolio_hold_em`` rows → holdings ``Reading``s, quarter-end dated.

    Each row's report-period label (``季度``) becomes its quarter-end ISO ``as_of`` so the store
    stays point-in-time and the graph windows are real dates; a row at or before ``floor`` (the
    stored watermark) is dropped, so an incremental re-pull adds only newly disclosed quarters.
    """

    readings: list[Reading] = []
    for row in rows:
        as_of = _quarter_end(str(row["季度"]))
        if floor is not None and as_of <= floor:
            continue
        readings.append(
            Reading(
                series=SERIES,
                key=f"{fund}/{row['股票名称']}",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "fund": fund,
                    "holding": str(row["股票名称"]),
                    "weight": float(row["占净值比例"]) / 100.0,
                },
            )
        )
    return readings


def _quarter_end(label: str) -> str:
    """A report-period label (``"2026年1季度股票投资明细"``) → its quarter-end ISO date.

    The label is the only date AkShare discloses per holding; an unrecognised one is a schema drift,
    not a silent mis-date, so it raises (degraded per fund by the resilience boundary) rather than
    poisoning the point-in-time store with a non-date ``as_of``.
    """

    match = _QUARTER_LABEL.match(label)
    if match is None:
        raise IngestError(f"unparseable holdings quarter label: {label!r}")
    return f"{match['year']}-{_QUARTER_END[match['quarter']]}"


def _first_year(since: str | None, fetched_at: str) -> int:
    """The earliest disclosure year to request: the watermark's year, else the run stamp's year."""

    return int((since or fetched_at)[:4])
