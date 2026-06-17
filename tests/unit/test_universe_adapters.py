"""The full-universe ingest adapters — fund_universe (all funds) + etf_scale (AUM).

These pin the survivorship machinery (``still_listed`` + ``delisting_disclosures``, which has no
live feed — a dead fund simply vanishes from the next pull) and the live AkShare ETF-scale column
mapping. The universe read's identity/lifecycle/scorecard shape and the offline replay are covered
in ``tests/unit/test_feed.py``.
"""

from __future__ import annotations

import pytest

from factor_scope.ingest import etf_scale, fund_universe
from factor_scope.ingest.fund_universe import delisting_disclosures, still_listed
from factor_scope.store import Reading

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"
FETCHED_AT = "2026-06-05T22:00:00Z"


def test_still_listed_excludes_a_fund_once_delisted() -> None:
    # A fund whose delisting date has passed is gone — it cannot be mapped, screened, or bought.
    assert still_listed("2025-12-31", "2026-06-05") is False
    # On the delisting day itself the fund is already untradable.
    assert still_listed("2026-06-05", "2026-06-05") is False


def test_still_listed_keeps_a_since_delisted_fund_at_an_old_as_of() -> None:
    # Survivorship-awareness cuts both ways: at a date *before* its delisting the fund was alive,
    # and a point-in-time universe query must still include it.
    assert still_listed("2025-12-31", "2025-12-01") is True


def test_missing_delisting_means_listed() -> None:
    assert still_listed("", "2026-06-05") is True


def _universe_row(code: str, as_of: str, delisting: str = "") -> Reading:
    return Reading(
        series=fund_universe.SERIES,
        key=code,
        as_of=as_of,
        fetched_at=f"{as_of}T22:00:00Z",
        payload={
            "name": f"fund-{code}",
            "type": "ETF",
            "on_exchange": True,
            "inception": "2021-01-20",
            "delisting": delisting,
            "fee": 0.005,
            "tracking_error": 0.01,
            "top10_weight": 0.5,
            "valid": True,
        },
    )


def test_a_fund_the_feed_stopped_listing_is_disclosed_delisted() -> None:
    # The live universe has no delisting feed — a dead fund simply vanishes from the next pull.
    # Given the latest row per fund, the one whose row predates tonight was dropped by the feed,
    # so it is disclosed delisted as of tonight; the refreshed fund is untouched.
    rows = delisting_disclosures(
        [_universe_row("561010", AS_OF), _universe_row("159000", "2026-06-04")],
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
    )
    assert [r.key for r in rows] == ["159000"]
    assert rows[0].as_of == AS_OF
    assert rows[0].payload["delisting"] == AS_OF
    assert rows[0].payload["name"] == "fund-159000"  # identity carried; only the lifecycle changes


def test_an_already_disclosed_delisting_is_never_rewritten() -> None:
    # The fund died long ago with a real delisting date on record — a later run must not move it.
    rows = delisting_disclosures(
        [_universe_row("159999", "2026-06-04", delisting="2025-12-31")],
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
    )
    assert rows == []


def test_the_disclosure_is_idempotent_within_a_night() -> None:
    first = delisting_disclosures(
        [_universe_row("561010", AS_OF), _universe_row("159000", "2026-06-04")],
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
    )
    assert delisting_disclosures(first, as_of=AS_OF, fetched_at=FETCHED_AT) == []


def test_a_silent_feed_discloses_nothing() -> None:
    # Zero rows from tonight means the feed was down, not that every fund died — a vanished fund
    # is only evidence when the feed actually spoke. Degrade, never infer a mass delisting.
    rows = delisting_disclosures(
        [_universe_row("561010", "2026-06-04"), _universe_row("159000", "2026-06-04")],
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
    )
    assert rows == []


def test_etf_scale_maps_the_akshare_spot_columns() -> None:
    # the live ETF spot feed (代码 / 数据日期 / 总市值 in 元 / 最新份额 in 份) maps to the same
    # Reading shape as the fixture (aum/shares in 亿), pinned offline so the mapping is covered
    # without the network. 数据日期 arrives as a timestamp; only its date is kept, and the exchange
    # is read off the code prefix (5… is Shanghai, otherwise Shenzhen).
    rows = [
        {"代码": "561010", "数据日期": "2026-06-15 00:00:00", "总市值": 6_800_000_000.0,
         "最新份额": 4_000_000_000.0},
        {"代码": "159755", "数据日期": "2026-06-15 00:00:00", "总市值": 4_600_000_000.0,
         "最新份额": 4_200_000_000.0},
    ]
    sse, szse = etf_scale._from_rows(rows, fetched_at=FETCHED_AT)
    assert sse.series == etf_scale.SERIES
    assert sse.key == "561010"
    assert sse.as_of == "2026-06-15"  # the feed's own date, time component dropped
    assert sse.payload == {"exchange": "sse", "aum": 68.0, "shares": 40.0}
    assert szse.key == "159755"
    assert szse.payload == {"exchange": "szse", "aum": 46.0, "shares": 42.0}
