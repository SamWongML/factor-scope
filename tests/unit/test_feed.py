"""The ingest transport seam — the committed-cassette replay that backs the offline ingest.

These pin :class:`CassetteFeed`: it replays the recorded responses under ``data/fixtures/cassettes``
into the same :class:`~factor_scope.store.Reading` rows the universe loop + price reconciliation
consume, honours the incremental ``since`` watermark on the per-fund series, and never touches the
network. ``get_feed`` selects the cassettes offline and the live adapters online.
"""

from __future__ import annotations

import pytest

from factor_scope.config import Config
from factor_scope.ingest.feed import CassetteFeed, LiveFeed, get_feed

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"
FETCHED_AT = "2026-06-05T22:00:00Z"


def _feed() -> CassetteFeed:
    return CassetteFeed(Config().fixtures_dir / "cassettes")


def test_get_feed_selects_cassettes_offline_and_the_adapters_online() -> None:
    assert isinstance(get_feed(Config()), CassetteFeed)  # the suite forces offline
    assert isinstance(get_feed(Config(source="live")), LiveFeed)


def test_universe_carries_identity_lifecycle_and_scorecard_inputs() -> None:
    by_code = {r.key: r for r in _feed().universe(as_of=AS_OF, fetched_at=FETCHED_AT)}
    etf = by_code["561010"]
    assert etf.series == "fund_universe"
    assert etf.as_of == AS_OF  # membership is stamped point-in-time with the run, not the feed date
    assert etf.payload["on_exchange"] is True
    assert etf.payload["fee"] == 0.005
    assert etf.payload["valid"] is True
    # an off-exchange fund with no scorecard inputs is kept but flagged, never dropped
    assert by_code["000001"].payload["valid"] is False
    # the delisted fund keeps its delisting date for the survivorship-aware universe
    assert by_code["159999"].payload["delisting"] == "2025-12-31"


def test_per_fund_series_honour_the_since_watermark() -> None:
    feed = _feed()
    full = feed.activity("561010", fetched_at=FETCHED_AT)
    assert full and full[0].series == "trading_activity"
    floor = full[-2].as_of
    incremental = feed.activity("561010", fetched_at=FETCHED_AT, since=floor)
    assert [r.as_of for r in incremental] == [full[-1].as_of]  # only strictly-newer bars

    held = feed.holdings("561160", fetched_at=FETCHED_AT)
    assert held and all(r.key.startswith("561160/") for r in held)
    assert feed.holdings("561160", fetched_at=FETCHED_AT, since=held[-1].as_of) == []


def test_price_sources_replays_three_corroborating_legs() -> None:
    legs = _feed().price_sources("561010", fetched_at=FETCHED_AT)
    assert [leg[0].payload["source"] for leg in legs] == ["akshare", "baostock", "mootdx"]
    assert all(len(leg) == len(legs[0]) for leg in legs)  # one shared recorded history per leg
    assert legs[0][-1].as_of == AS_OF


def test_price_sources_is_empty_for_an_unpriced_fund() -> None:
    assert _feed().price_sources("510300", fetched_at=FETCHED_AT) == [[], [], []]
