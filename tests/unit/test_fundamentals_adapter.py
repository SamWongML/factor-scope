"""The fundamentals ingest adapter — per-fund PE, the valuation surface.

Each row is keyed by code and stamped with the valuation feed's own date, carrying ``pe`` (市盈率).
A malformed header or non-numeric value is a hard parse error.
"""

from __future__ import annotations

from datetime import date

import pytest

from factor_scope.ingest import fundamentals
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"


def test_fundamentals_carries_pe_stamped_with_the_feed_date() -> None:
    readings = fundamentals.parse(
        "code,as_of,pe\n561010,2026-05-29,42.5\n", fetched_at=FETCHED_AT
    )
    first = readings[0]
    assert first.series == fundamentals.SERIES
    assert first.key == "561010"
    assert first.as_of == "2026-05-29"
    assert first.payload == {"pe": 42.5}


def test_fundamentals_rejects_a_malformed_header() -> None:
    with pytest.raises(IngestError):
        fundamentals.parse("code,as_of\n561010,2026-05-29\n", fetched_at="t")


def test_fundamentals_rejects_a_non_numeric_value() -> None:
    with pytest.raises(IngestError):
        fundamentals.parse("code,as_of,pe\n561010,2026-05-29,n/a\n", fetched_at=FETCHED_AT)


def test_fundamentals_maps_the_akshare_valuation_columns() -> None:
    # the live CSI index valuation feed (日期 as a date, 市盈率2 the trailing-12-month multiple)
    # maps to a Reading keyed by the *fund* code, not the index, so the valuation factor ranks
    # the fund's own basket. 市盈率1 (the static multiple) is present but ignored. Pinned offline
    # so the mapping is covered without the network.
    bars = [{"日期": date(2026, 5, 30), "市盈率1": 50.0, "市盈率2": 44.1}]
    reading = fundamentals._from_bars("561010", bars, fetched_at=FETCHED_AT)[0]
    assert reading.key == "561010"
    assert reading.as_of == "2026-05-30"
    assert reading.payload == {"pe": 44.1}
