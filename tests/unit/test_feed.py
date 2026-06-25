"""The ingest transport seam — the committed-cassette replay that backs the offline ingest.

These pin :class:`CassetteFeed`: it replays the recorded responses under ``data/fixtures/cassettes``
into the same :class:`~factor_scope.store.Reading` rows the universe loop + price reconciliation
consume, honours the incremental ``since`` watermark on the per-fund series, and never touches the
network. ``get_feed`` selects the cassettes offline and the live adapters online.
"""

from __future__ import annotations

import pytest

from factor_scope.config import Config
from factor_scope.ingest import feed as feed_mod
from factor_scope.ingest.feed import CassetteFeed, LiveFeed, get_feed

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"
FETCHED_AT = "2026-06-05T22:00:00Z"


def _feed() -> CassetteFeed:
    return CassetteFeed(Config().fixtures_dir / "cassettes")


def test_get_feed_selects_cassettes_offline_and_the_adapters_online() -> None:
    assert isinstance(get_feed(Config(), store=None), CassetteFeed)  # the suite forces offline
    assert isinstance(get_feed(Config(source="live"), store=None), LiveFeed)


def test_get_feed_makes_the_live_feed_store_aware_and_the_cassette_store_agnostic() -> None:
    # The live feed carries the point-in-time store for the per-code spot/deep load-shape decision;
    # the offline cassette ignores it and replays recordings exactly.
    store = object()  # a stand-in store — the live feed only needs to hold it here
    live = get_feed(Config(source="live"), store=store)
    assert isinstance(live, LiveFeed)
    assert live._store is store
    assert isinstance(get_feed(Config(), store=store), CassetteFeed)  # offline drops the store


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


def test_live_feed_paces_between_per_fund_calls(monkeypatch) -> None:
    # Live, sequential per-fund calls to the rate-limited host are paced so the burst doesn't trip
    # the IP limiter; the delay is config-driven. (Offline cassettes never pace — see below.)
    paced: list[float] = []
    monkeypatch.setattr(feed_mod, "pace_between_calls", lambda seconds: paced.append(seconds))
    monkeypatch.setattr("factor_scope.ingest.etf_scale.fetch_spot_board", dict)
    monkeypatch.setattr(
        "factor_scope.ingest.trading_activity.fetch_live",
        lambda board, code, *, fetched_at, since=None, impersonate="chrome": [],
    )
    live = get_feed(Config(source="live", live_pacing_seconds=0.7), store=None)
    assert isinstance(live, LiveFeed)
    live.activity("561010", fetched_at=FETCHED_AT)
    assert paced == [0.7]  # paced once with the configured delay, before the per-fund network call


def test_live_feed_threads_the_impersonation_profile_to_the_prices_leg(monkeypatch) -> None:
    # The EastMoney fingerprint is config-driven so it can be bumped when Chrome's TLS profile
    # drifts; get_feed must thread Config.eastmoney_impersonate down to the price NAV leg (and only
    # that leg — Baostock/Mootdx don't speak it).
    seen: dict[str, str] = {}

    def fake_price(code, *, fetched_at, since=None, impersonate="chrome"):
        seen["impersonate"] = impersonate
        return []

    monkeypatch.setattr("factor_scope.ingest.prices.fetch_live", fake_price)
    for leg in ("baostock", "mootdx"):
        monkeypatch.setattr(
            f"factor_scope.ingest.{leg}.fetch_live",
            lambda code, *, fetched_at, since=None: [],
        )
    live = get_feed(
        Config(source="live", eastmoney_impersonate="chrome131", live_pacing_seconds=0), store=None
    )
    live.price_sources("561010", fetched_at=FETCHED_AT)
    assert seen["impersonate"] == "chrome131"  # the configured profile reached the client boundary


def test_live_feed_threads_the_impersonation_profile_to_the_activity_leg(monkeypatch) -> None:
    # The activity history leg rides the same EastMoney K-line client as the NAV leg, so get_feed
    # must thread Config.eastmoney_impersonate down to it too — else a bumped profile fixes NAV but
    # leaves activity hitting the same host with the stale one (and tripping the shared breaker).
    seen: dict[str, str] = {}

    def fake_activity(board, code, *, fetched_at, since=None, impersonate="chrome"):
        seen["impersonate"] = impersonate
        return []

    monkeypatch.setattr("factor_scope.ingest.etf_scale.fetch_spot_board", dict)
    monkeypatch.setattr("factor_scope.ingest.trading_activity.fetch_live", fake_activity)
    live = get_feed(
        Config(source="live", eastmoney_impersonate="chrome131", live_pacing_seconds=0), store=None
    )
    live.activity("561010", fetched_at=FETCHED_AT)
    assert seen["impersonate"] == "chrome131"  # the configured profile reached the activity leg


def test_cassette_feed_never_paces() -> None:
    # Offline replay is the deterministic test mode — it must not sleep. CassetteFeed simply has no
    # pacing call; constructing the offline feed and reading from it never touches the pacer.
    assert not hasattr(_feed(), "_pace_seconds")


def test_price_sources_honour_the_since_watermark() -> None:
    # Prices are watermarked like the other per-fund series: a re-pull replays only sessions past
    # the floor, so the nightly re-pull stays incremental, not re-streaming the whole history.
    feed = _feed()
    full = feed.price_sources("561010", fetched_at=FETCHED_AT)[0]
    floor = full[-2].as_of
    incremental = feed.price_sources("561010", fetched_at=FETCHED_AT, since=floor)
    assert all([r.as_of for r in leg] == [full[-1].as_of] for leg in incremental)  # newer-only
