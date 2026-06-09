"""Daily per-fund trading activity — turnover and traded value, the crowding/illiquidity surface.

`trading_activity.csv → {code, as_of, turnover, amount}`. One row per fund per session, keyed by
code and stamped with the bar's own trading date. ``turnover`` is the daily turnover ratio (换手率,
the crowding signal); ``amount`` is the daily traded value (成交额, the Amihud-illiquidity input).
Both are read point-in-time against a fund's own history by the factor battery.

Live pulls AkShare's on-exchange ETF daily bar (``fund_etf_hist_em``) — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

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


def fetch_live(code: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull a fund's daily turnover + traded value via AkShare. Requires `live` + network."""

    import akshare as ak

    frame = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")
    readings: list[Reading] = []
    for _, row in frame.iterrows():
        readings.append(
            Reading(
                series=SERIES,
                key=code,
                as_of=str(row["日期"]),
                fetched_at=fetched_at,
                payload={"turnover": float(row["换手率"]), "amount": float(row["成交额"])},
            )
        )
    return readings
