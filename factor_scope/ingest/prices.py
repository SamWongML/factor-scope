"""Prices / fund-NAV adapter (CN). Fixture: `prices.csv → {code, as_of, nav}`.

Per-item gain comes from cost basis vs the current NAV pulled here. Live is AkShare's ETF history
(``fund_etf_hist_em``) — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "prices"
FIXTURE = "prices.csv"
_REQUIRED = ("code", "as_of", "nav")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        code = required_str(row, "code", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        nav = as_float(row, "nav", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES, key=code, as_of=as_of, fetched_at=fetched_at, payload={"nav": nav}
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def fetch_live(code: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull the latest daily NAV for one ETF via AkShare. Requires the `live` extra + network."""

    import akshare as ak

    frame = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")
    last = frame.iloc[-1]
    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(last["日期"]),
            fetched_at=fetched_at,
            payload={"nav": float(last["收盘"])},
        )
    ]
