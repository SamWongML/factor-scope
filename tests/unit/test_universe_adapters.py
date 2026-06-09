"""The full-universe ingest adapters — fund_universe (all funds) + etf_scale (AUM).

These pin the two new offline backends: the fund universe carries each fund's identity, lifecycle
(inception/delisting, for the survivorship-aware universe), and the per-fund scorecard inputs —
marking a fund ``valid=False`` when any scorecard input is missing rather than dropping it. The
ETF-scale feed carries AUM/shares per exchange (SSE/SZSE), stamped with its own disclosure date.
"""

from __future__ import annotations

import pytest

from factor_scope.ingest import etf_scale, fund_universe
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"
FETCHED_AT = "2026-06-05T22:00:00Z"

_UNIVERSE = (
    "code,name,type,on_exchange,inception,delisting,fee,tracking_error,top10_weight\n"
    "561010,光通信ETF,ETF,true,2021-01-20,,0.005,0.010,0.62\n"
    "000001,华夏成长混合,混合型,false,2001-12-18,,,,\n"
    "159999,退市光伏ETF,ETF,true,2018-03-01,2025-12-31,0.005,0.020,0.50\n"
)

_SCALE = (
    "code,as_of,exchange,aum,shares\n"
    "561010,2026-05-31,sse,68,40\n"
    "159755,2026-05-31,szse,46,42\n"
)


def test_fund_universe_carries_identity_lifecycle_and_scorecard_inputs() -> None:
    readings = fund_universe.parse(_UNIVERSE, as_of=AS_OF, fetched_at=FETCHED_AT)
    by_code = {r.key: r for r in readings}
    etf = by_code["561010"]
    assert etf.series == fund_universe.SERIES
    assert etf.as_of == AS_OF  # universe membership is stamped point-in-time with the run
    assert etf.payload["name"] == "光通信ETF"
    assert etf.payload["on_exchange"] is True
    assert etf.payload["inception"] == "2021-01-20"
    assert etf.payload["delisting"] == ""  # still listed
    assert etf.payload["fee"] == 0.005
    assert etf.payload["top10_weight"] == 0.62
    assert etf.payload["valid"] is True


def test_fund_universe_keeps_a_fund_with_missing_scorecard_inputs_but_flags_it() -> None:
    by_code = {r.key: r for r in fund_universe.parse(_UNIVERSE, as_of=AS_OF, fetched_at=FETCHED_AT)}
    off = by_code["000001"]
    assert off.payload["on_exchange"] is False
    assert off.payload["fee"] is None  # missing, not dropped
    assert off.payload["tracking_error"] is None
    assert off.payload["valid"] is False  # incomplete scorecard inputs degrade, never raise


def test_fund_universe_captures_the_delisting_date_for_survivorship() -> None:
    by_code = {r.key: r for r in fund_universe.parse(_UNIVERSE, as_of=AS_OF, fetched_at=FETCHED_AT)}
    assert by_code["159999"].payload["delisting"] == "2025-12-31"


def test_fund_universe_rejects_a_malformed_header() -> None:
    with pytest.raises(IngestError):
        fund_universe.parse("code,name\n561010,x\n", as_of=AS_OF, fetched_at=FETCHED_AT)


def test_etf_scale_carries_aum_per_exchange_stamped_with_its_own_date() -> None:
    readings = etf_scale.parse(_SCALE, fetched_at=FETCHED_AT)
    by_code = {r.key: r for r in readings}
    sse = by_code["561010"]
    assert sse.series == etf_scale.SERIES
    assert sse.as_of == "2026-05-31"  # the scale feed's own disclosure date, not the run date
    assert sse.payload["exchange"] == "sse"
    assert sse.payload["aum"] == 68.0
    assert sse.payload["shares"] == 40.0
    assert by_code["159755"].payload["exchange"] == "szse"
