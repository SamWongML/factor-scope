"""The fundamentals ingest adapter — per-fund PE, the valuation surface.

Each row is keyed by the fund code and stamped with the valuation feed's own date, carrying ``pe``
(市盈率). This pins the live AkShare column mapping; the offline replay is covered in
``tests/unit/test_feed.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from factor_scope.ingest import fundamentals

pytestmark = pytest.mark.unit

FETCHED_AT = "2026-06-05T22:00:00Z"


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
