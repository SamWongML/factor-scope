"""Macro series adapter (FRED). Fixture: `fred.csv → {series_id, as_of, value}`.

The book-wide macro/liquidity dial: rates, real rates, breakevens, the dollar,
and Fed liquidity. Live is ``fredapi`` — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "fred"
FIXTURE = "fred.csv"
_REQUIRED = ("series_id", "as_of", "value")

# The book-wide macro dial (rates / real rate / breakeven / dollar / liquidity).
DEFAULT_SERIES = ("DGS10", "DFII10", "T10YIE", "DTWEXBGS", "DEXCHUS", "WALCL")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        series_id = required_str(row, "series_id", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        value = as_float(row, "value", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES,
                key=series_id,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"series_id": series_id, "value": value},
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def fetch_live(
    series_id: str, *, fetched_at: str, api_key: str | None = None
) -> list[Reading]:  # pragma: no cover - opt-in
    """Pull the latest observation of one FRED series. Requires `live` + an API key + network."""

    from fredapi import Fred

    series = Fred(api_key=api_key).get_series(series_id).dropna()
    return [
        Reading(
            series=SERIES,
            key=series_id,
            as_of=str(series.index[-1].date()),
            fetched_at=fetched_at,
            payload={"series_id": series_id, "value": float(series.iloc[-1])},
        )
    ]
