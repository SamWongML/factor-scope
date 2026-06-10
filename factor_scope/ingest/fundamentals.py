"""Per-fund valuation history — the underlying basket's PE, the valuation surface.

`fundamentals.csv → {code, as_of, pe}`. One row per fund per disclosure, keyed by code and stamped
with the valuation feed's own trading date. ``pe`` (市盈率) is the tracked basket's earnings
multiple; the valuation factor ranks it point-in-time against the fund's own history (a stretched
multiple is the anti-hype overvaluation gauge).

Live pulls AkShare's index valuation history (``index_value_hist_funddb``) for the fund's basket —
never called in CI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "fundamentals"
FIXTURE = "fundamentals.csv"
_REQUIRED = ("code", "as_of", "pe")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        readings.append(
            Reading(
                series=SERIES,
                key=required_str(row, "code", line_no, SERIES),
                as_of=required_str(row, "as_of", line_no, SERIES),
                fetched_at=fetched_at,
                payload={"pe": as_float(row, "pe", line_no, SERIES)},
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def _from_bars(code: str, bars: Iterable[Mapping[str, Any]], *, fetched_at: str) -> list[Reading]:
    """Map AkShare's index valuation bars (日期 / 市盈率) to Readings — the pure core."""

    return [
        Reading(
            series=SERIES,
            key=code,
            as_of=str(bar["日期"]),
            fetched_at=fetched_at,
            payload={"pe": float(bar["市盈率"])},
        )
        for bar in bars
    ]


def fetch_live(code: str, *, fetched_at: str) -> list[Reading]:  # pragma: no cover - live path
    """Pull a fund basket's PE/PB history via AkShare. Requires the `live` extra + network."""

    import akshare as ak

    frame = ak.index_value_hist_funddb(symbol=code)
    return _from_bars(code, (bar for _, bar in frame.iterrows()), fetched_at=fetched_at)
