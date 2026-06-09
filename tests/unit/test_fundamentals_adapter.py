"""The fundamentals ingest adapter — per-fund PE/PB, the valuation surface.

Each row is keyed by code and stamped with the valuation feed's own date, carrying ``pe`` (市盈率)
and ``pb`` (市净率). A malformed header or non-numeric value is a hard parse error.
"""

from __future__ import annotations

import pytest

from factor_scope.ingest import fundamentals
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"


def test_fundamentals_carries_pe_and_pb_stamped_with_the_feed_date() -> None:
    readings = fundamentals.parse(
        "code,as_of,pe,pb\n561010,2026-05-29,42.5,5.40\n", fetched_at=FETCHED_AT
    )
    first = readings[0]
    assert first.series == fundamentals.SERIES
    assert first.key == "561010"
    assert first.as_of == "2026-05-29"
    assert first.payload == {"pe": 42.5, "pb": 5.40}


def test_fundamentals_rejects_a_malformed_header() -> None:
    with pytest.raises(IngestError):
        fundamentals.parse("code,as_of,pe\n561010,2026-05-29,42.5\n", fetched_at="t")


def test_fundamentals_rejects_a_non_numeric_value() -> None:
    with pytest.raises(IngestError):
        fundamentals.parse("code,as_of,pe,pb\n561010,2026-05-29,n/a,5.4\n", fetched_at=FETCHED_AT)


def test_fundamentals_maps_the_akshare_valuation_columns() -> None:
    bars = [{"日期": "2026-05-30", "市盈率": "44.1", "市净率": "5.6"}]
    reading = fundamentals._from_bars("561010", bars, fetched_at=FETCHED_AT)[0]
    assert reading.key == "561010"
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"pe": 44.1, "pb": 5.6}
