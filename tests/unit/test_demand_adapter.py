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
    bars = [{"日期": "2026-03-31", "当月环比": "0.12"}]
    reading = demand._from_bars(bars, fetched_at=FETCHED_AT)[0]
    assert reading.key == demand.KEY
    assert reading.as_of == "2026-03-31"
    assert reading.payload == {"revision": 0.12}
