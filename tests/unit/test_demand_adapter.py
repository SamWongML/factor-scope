"""The demand ingest adapter — the book-wide end-demand dial.

Every row collapses to one book-wide key and carries the period's end-demand ``revision``. A
malformed header or non-numeric value is a hard parse error.
"""

from __future__ import annotations

import pytest

from factor_scope.ingest import demand
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"


def test_demand_collapses_to_one_book_wide_series_with_the_revision() -> None:
    readings = demand.parse(
        "as_of,revision\n2026-03-31,0.08\n2026-06-30,-0.02\n", fetched_at=FETCHED_AT
    )
    assert {r.key for r in readings} == {demand.KEY}
    assert readings[0].series == demand.SERIES
    assert readings[0].as_of == "2026-03-31"
    assert readings[0].payload == {"revision": 0.08}
    assert readings[1].payload == {"revision": -0.02}


def test_demand_rejects_a_malformed_header() -> None:
    with pytest.raises(IngestError):
        demand.parse("as_of\n2026-03-31\n", fetched_at="t")


def test_demand_rejects_a_non_numeric_value() -> None:
    with pytest.raises(IngestError):
        demand.parse("as_of,revision\n2026-03-31,n/a\n", fetched_at=FETCHED_AT)


def test_demand_maps_the_akshare_release_columns() -> None:
    # the live release (日期 / 今值 / 前值, all the YoY growth rate) maps to the change in that
    # rate — accelerating vs fading demand. The latest row whose actual (今值) has not printed
    # yet is NaN and is dropped. Pinned offline so the mapping is covered without the network.
    bars = [
        {"日期": "2026-08-15", "今值": 5.7, "预测值": 6.0, "前值": 6.8},
        {"日期": "2026-09-15", "今值": float("nan"), "预测值": float("nan"), "前值": 5.7},
    ]
    readings = demand._from_bars(bars, fetched_at=FETCHED_AT)
    assert len(readings) == 1  # the unreleased forecast row is dropped, not raised on
    reading = readings[0]
    assert reading.key == demand.KEY
    assert reading.as_of == "2026-08-15"
    assert reading.payload["revision"] == pytest.approx(5.7 - 6.8)
