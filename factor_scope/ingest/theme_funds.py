"""Theme-funds adapter — candidate funds for the emerging funnel's Stage B (spec §07).

`theme_funds.csv → {theme, code, name, as_of, methodology, fee, aum, tracking_error, top10_weight,
crowding}`.
Each row is one candidate CN fund/ETF for a theme, with the fixed-scorecard inputs; keyed by fund
code and stamped with its research ``as_of``. The candidate's *holdings* are ingested through the
ordinary ``fund_holdings`` feed, so the §05 look-through can measure overlap-with-core without any
new graph logic. Live is AkShare's theme-fund universe — opt-in, never called in CI.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "theme_funds"
FIXTURE = "theme_funds.csv"
_REQUIRED = (
    "theme",
    "code",
    "name",
    "as_of",
    "methodology",
    "fee",
    "aum",
    "tracking_error",
    "top10_weight",
    "crowding",
)


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        theme = required_str(row, "theme", line_no, SERIES)
        code = required_str(row, "code", line_no, SERIES)
        name = required_str(row, "name", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        readings.append(
            Reading(
                series=SERIES,
                key=code,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "theme": theme,
                    "name": name,
                    "methodology": as_float(row, "methodology", line_no, SERIES),
                    "fee": as_float(row, "fee", line_no, SERIES),
                    "aum": as_float(row, "aum", line_no, SERIES),
                    "tracking_error": as_float(row, "tracking_error", line_no, SERIES),
                    "top10_weight": as_float(row, "top10_weight", line_no, SERIES),
                    "crowding": as_float(row, "crowding", line_no, SERIES),
                },
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)
