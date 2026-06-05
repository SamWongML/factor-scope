"""Quarterly fund/ETF holdings. `fund_holdings.csv → {fund, as_of, holding, weight}`.

These rows become the connection-graph edges in Phase 3 (the exact look-through). Each ``(fund,
holding)`` pair is its own point-in-time key so a quarter's disclosure never overwrites a prior one.
Live is AkShare's ``fund_portfolio_hold_em`` — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "fund_holdings"
FIXTURE = "fund_holdings.csv"
_REQUIRED = ("fund", "as_of", "holding", "weight")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        fund = required_str(row, "fund", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        holding = required_str(row, "holding", line_no, SERIES)
        weight = as_float(row, "weight", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES,
                key=f"{fund}/{holding}",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"fund": fund, "holding": holding, "weight": weight},
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def fetch_live(fund: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull a fund's latest disclosed stock holdings via AkShare. Requires `live` + network."""

    import akshare as ak

    frame = ak.fund_portfolio_hold_em(symbol=fund, date="2026")
    readings: list[Reading] = []
    for _, row in frame.iterrows():
        readings.append(
            Reading(
                series=SERIES,
                key=f"{fund}/{row['股票名称']}",
                as_of=str(row["季度"]),
                fetched_at=fetched_at,
                payload={
                    "fund": fund,
                    "holding": str(row["股票名称"]),
                    "weight": float(row["占净值比例"]) / 100.0,
                },
            )
        )
    return readings
