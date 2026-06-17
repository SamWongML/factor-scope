"""The book-wide end-demand dial — the leading driver's order/capex revisions.

`demand.csv → {as_of, revision}`. One book-wide series (every row shares the same key), stamped with
the release's own date. ``revision`` is the period-over-period change in end-demand orders/capex
(the leading driver behind the theme, e.g. hyperscaler capex for the AI/optical chain); the demand
factor ranks the latest revision point-in-time against its own history — accelerating revisions are
a demand tailwind, fading ones a headwind.

Live reads AkShare's industrial value-added YoY release
(``macro_china_industrial_production_yoy``) and takes the change in the growth rate as the
revision — never called in CI.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "demand"
FIXTURE = "demand.csv"
KEY = "end_demand"  # one book-wide series — the leading driver behind the whole theme chain
_REQUIRED = ("as_of", "revision")


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        readings.append(
            Reading(
                series=SERIES,
                key=KEY,
                as_of=required_str(row, "as_of", line_no, SERIES),
                fetched_at=fetched_at,
                payload={"revision": as_float(row, "revision", line_no, SERIES)},
            )
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)


def _from_bars(bars: Iterable[Mapping[str, Any]], *, fetched_at: str) -> list[Reading]:
    """Map the release rows (日期 / 今值 / 前值, the YoY growth rate) to Readings — the pure core.

    ``revision`` is the change in that rate (今值 − 前值): positive is accelerating demand,
    negative fading. The latest period's actual (今值) can be unreleased (NaN); that row is
    dropped, not raised.
    """

    readings: list[Reading] = []
    for bar in bars:
        current = float(bar["今值"])
        if math.isnan(current):
            continue
        previous = float(bar["前值"])
        if math.isnan(previous):
            continue
        readings.append(
            Reading(
                series=SERIES,
                key=KEY,
                as_of=str(bar["日期"]),
                fetched_at=fetched_at,
                payload={"revision": current - previous},
            )
        )
    return readings


def fetch_live(*, fetched_at: str) -> list[Reading]:  # pragma: no cover - live path
    """Pull the end-demand orders/capex revision via AkShare. Requires `live` + network."""

    import akshare as ak

    frame = ak.macro_china_industrial_production_yoy()
    return _from_bars((bar for _, bar in frame.iterrows()), fetched_at=fetched_at)
