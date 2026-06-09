"""On-exchange ETF scale — AUM and share count per exchange.

`etf_scale.csv → {code, as_of, exchange, aum, shares}`. One row per ETF per disclosure, keyed by
code and stamped with the scale feed's own ``as_of`` (not the run date — AUM is disclosed on its own
calendar). ``aum`` is the net asset value in 亿 (100M CNY), matching the scorecard's AUM input; it
is the size half of the per-fund scorecard inputs the universe carries.

Live unions AkShare's ``fund_etf_scale_sse`` (Shanghai) and ``fund_etf_scale_szse`` (Shenzhen) —
opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "etf_scale"
FIXTURE = "etf_scale.csv"
_REQUIRED = ("code", "as_of", "exchange", "aum", "shares")


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
                    "exchange": required_str(row, "exchange", line_no, SERIES),
                    "aum": as_float(row, "aum", line_no, SERIES),
                    "shares": as_float(row, "shares", line_no, SERIES),
                },
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def fetch_live(*, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Union the Shanghai + Shenzhen ETF-scale tables. Requires `live` + network."""

    import akshare as ak

    readings: list[Reading] = []
    for exchange, frame in (("sse", ak.fund_etf_scale_sse()), ("szse", ak.fund_etf_scale_szse())):
        for _, row in frame.iterrows():
            readings.append(
                Reading(
                    series=SERIES,
                    key=str(row["基金代码"]),
                    as_of=str(row["数据日期"]),
                    fetched_at=fetched_at,
                    payload={
                        "exchange": exchange,
                        "aum": float(row["基金规模"]),
                        "shares": float(row["流通份额"]),
                    },
                )
            )
    return readings
