"""The market seam — config-selected adapters behind source protocols.

The ``Market`` / ``UniverseSource`` / ``ThemeSource`` / ``PriceSource`` seams keep the engine
no longer hard-wired to A-share. A-share is the first concrete adapter; these pin the seam: the
market is selected by name, a fake market made of fake sources drives the whole pipeline, and the
A-share fixture gather still produces every series the artifact reads (no behaviour change).
"""

from __future__ import annotations

import pytest

from factor_scope.config import Config
from factor_scope.contract import ListName
from factor_scope.ingest.feed import Feed
from factor_scope.markets import ComposedMarket, get_market
from factor_scope.markets.ashare import AShareUniverse, _fetch_codes_by_priority
from factor_scope.pipeline import build_dashboard
from factor_scope.store import PointInTimeStore, Reading

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"
FETCHED_AT = "2026-06-05T22:00:00Z"
_SEASONED = "2021-01-20"  # well past the seasoning window as of AS_OF


def test_get_market_selects_ashare_by_name() -> None:
    market = get_market("ashare")
    assert market.name == "ashare"


def test_get_market_rejects_an_unknown_market() -> None:
    with pytest.raises(ValueError, match="unknown market"):
        get_market("nyse")


def test_config_defaults_to_the_ashare_market() -> None:
    assert Config().market == "ashare"


def test_ashare_fixture_gather_keeps_every_series() -> None:
    # No behaviour change: the A-share adapter must still emit every series the artifact reads, so a
    # fixtures run produces the same store the prior monolithic gather did.
    readings = get_market("ashare").gather(Config(), as_of=AS_OF)
    series = {r.series for r in readings}
    assert series == {
        "positions",
        "fund_universe",
        "etf_scale",
        "prices",
        "fund_holdings",
        "trading_activity",
        "fundamentals",
        "fred",
        "demand",
        "edgar",
        "calls",
        "themes",
    }
    # positions are stamped with the run's as_of (point-in-time), prices keep their own dates
    assert all(r.as_of == AS_OF for r in readings if r.series == "positions")


class _FakeUniverse:
    """A one-position book, in the UniverseSource shape."""

    def gather(
        self,
        config: Config,
        *,
        as_of: str,
        fetched_at: str,
        feed: Feed,
        store: PointInTimeStore | None = None,
    ) -> list[Reading]:
        return [
            Reading(
                series="positions",
                key="FAKE1",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "name": "Fake Fund",
                    "cost_basis": 1.0,
                    "shares": 100.0,
                    "list": "holdings",
                },
            )
        ]


class _FakePrices:
    """One NAV for the universe's codes, in the PriceSource shape."""

    def gather(
        self, config: Config, codes: list[str], *, as_of: str, fetched_at: str, feed: Feed
    ) -> list[Reading]:
        return [
            Reading(
                series="prices",
                key=code,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"nav": 1.5, "source": "fake"},
            )
            for code in codes
        ]


class _FakeThemes:
    """No emerging themes, in the ThemeSource shape."""

    def gather(self, config: Config, *, as_of: str, fetched_at: str) -> list[Reading]:
        return []


def test_a_fake_market_drives_the_whole_pipeline() -> None:
    # The acceptance seam: a market composed of fake sources runs build_dashboard end to end and
    # yields a valid artifact — proving the pipeline targets the protocols, not A-share.
    market = ComposedMarket(
        name="fake", universe=_FakeUniverse(), prices=_FakePrices(), themes=_FakeThemes()
    )
    dash = build_dashboard(Config(), market=market)
    assert [item.item for item in dash.items] == ["Fake Fund"]
    item = dash.items[0]
    assert item.list_name is ListName.HOLDINGS
    assert item.gain == pytest.approx(0.5)  # (1.5 nav - 1.0 cost) / 1.0


def test_composed_market_threads_one_shared_feed_to_its_sources() -> None:
    # ComposedMarket builds a single feed per run and hands it to the universe and price sources —
    # the same one-feed-per-run wiring AShareMarket uses — so a feed-driven source never re-creates
    # the transport, and the source protocols carry the feed the sources read from.
    feeds: list[Feed] = []

    class _FeedAwareUniverse:
        def gather(
            self,
            config: Config,
            *,
            as_of: str,
            fetched_at: str,
            feed: Feed,
            store: PointInTimeStore | None = None,
        ) -> list[Reading]:
            feeds.append(feed)
            return []

    class _FeedAwarePrices:
        def gather(
            self, config: Config, codes: list[str], *, as_of: str, fetched_at: str, feed: Feed
        ) -> list[Reading]:
            feeds.append(feed)
            return []

    market = ComposedMarket(
        name="fake", universe=_FeedAwareUniverse(), prices=_FeedAwarePrices(), themes=_FakeThemes()
    )
    market.gather(Config(), as_of=AS_OF)
    assert len(feeds) == 2  # threaded to both sources: universe + prices
    assert feeds[0] is feeds[1]  # the same feed instance, one per run
    assert isinstance(feeds[0], Feed)  # the real transport (offline → CassetteFeed)


# --- Tier-priority streaming: book/core seed before probation when the deep-pull cap binds ---


def _univ(code: str, *, on_exchange: bool = True, inception: str = _SEASONED) -> Reading:
    """A fund-universe membership row — the tier screen reads ``on_exchange`` + ``inception``."""

    return Reading(
        series="fund_universe",
        key=code,
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
        payload={"name": code, "on_exchange": on_exchange, "inception": inception},
    )


def _scale(code: str, *, aum: float, amount: float) -> Reading:
    """An ETF-scale row — the cheap spot-board fields (AUM + traded value) the tier screen reads."""

    return Reading(
        series="etf_scale",
        key=code,
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
        payload={"aum": aum, "amount": amount},
    )


def test_fetch_codes_stream_book_and_core_before_probation() -> None:
    # The stream order the store-aware feed self-caps against: the book (the breaker's required set)
    # and the core tier come first, then probation; dead and off-exchange funds earn no fetch. So
    # when the per-run deep-pull cap binds, the most important funds are seeded before the tail.
    readings = [
        _univ("BOOK1"), _scale("BOOK1", aum=3.0, amount=1.0),  # held → book, though probation-tier
        _univ("CORE1"), _scale("CORE1", aum=68.0, amount=3.0),  # core
        _univ("PROB1"), _scale("PROB1", aum=3.0, amount=1.0),  # probation (small but trading)
        _univ("DEAD1"), _scale("DEAD1", aum=0.3, amount=0.0),  # dead → never fetched
        _univ("CORE2"), _scale("CORE2", aum=50.0, amount=2.0),  # core
        # off-exchange → no per-fund fetch even though its size would otherwise be core
        _univ("OFF1", on_exchange=False), _scale("OFF1", aum=68.0, amount=3.0),
    ]
    ordered = _fetch_codes_by_priority(readings, AS_OF, book={"BOOK1"})
    assert ordered == ["BOOK1", "CORE1", "CORE2", "PROB1"]  # book∪core (sorted), then probation
    assert "DEAD1" not in ordered and "OFF1" not in ordered  # the dead/off-exchange earn no fetch


def test_a_held_code_off_the_on_exchange_screen_still_streams_with_core_priority() -> None:
    # A held fund the on-exchange screen never saw — off-exchange, or not yet on the universe board
    # — is still the breaker's required set. It must stream with book/core priority so the per-run
    # cap seeds it before the probation tail, not leave its deep pull to the price loop.
    readings = [
        _univ("CORE1"), _scale("CORE1", aum=68.0, amount=3.0),  # core
        _univ("PROB1"), _scale("PROB1", aum=3.0, amount=1.0),  # probation
        # OFFBOOK is held but trades off-exchange; MISSING is held but absent from the universe.
        _univ("OFFBOOK", on_exchange=False), _scale("OFFBOOK", aum=68.0, amount=3.0),
    ]
    ordered = _fetch_codes_by_priority(readings, AS_OF, book={"OFFBOOK", "MISSING"})
    assert "OFFBOOK" in ordered and "MISSING" in ordered  # neither held code is dropped
    assert ordered.index("OFFBOOK") < ordered.index("PROB1")  # off-exchange held before probation
    assert ordered.index("MISSING") < ordered.index("PROB1")  # universe-absent held too


class _RecordingFeed:
    """A feed recording the order its per-fund (activity) leg is streamed — the deep-pull order."""

    def __init__(self, universe: list[Reading], scale: list[Reading]) -> None:
        self._universe = universe
        self._scale = scale
        self.activity_order: list[str] = []

    def universe(self, *, as_of: str, fetched_at: str) -> list[Reading]:
        return self._universe

    def etf_scale(self, *, fetched_at: str) -> list[Reading]:
        return self._scale

    def holdings(self, fund: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        return []

    def activity(self, code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        self.activity_order.append(code)  # the deep-pull entry point — record the stream order
        return []

    def valuation(self, code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        return []

    def price_sources(
        self, code: str, *, fetched_at: str, since: str | None = None
    ) -> list[list[Reading]]:
        return [[], [], []]

    def log_backfill_deferral(self) -> None: ...


def test_universe_gather_streams_core_before_probation() -> None:
    # The market hands codes to the feed in tier priority: even with probation listed first in the
    # universe read, the per-fund loop seeds the core fund before the probation one — so the feed's
    # greedy self-cap spends its budget on core first. The held book (the breaker's required set,
    # from positions.csv) leads the stream; the probation tail is seeded last.
    feed = _RecordingFeed(
        universe=[_univ("PROB1"), _univ("CORE1")],  # probation deliberately first in feed order
        scale=[_scale("PROB1", aum=3.0, amount=1.0), _scale("CORE1", aum=68.0, amount=3.0)],
    )
    AShareUniverse().gather(Config(), as_of=AS_OF, fetched_at=FETCHED_AT, feed=feed, store=None)
    order = feed.activity_order
    assert order.index("CORE1") < order.index("PROB1")  # core before probation, feed order aside
    assert order[-1] == "PROB1"  # the probation tail is seeded last, after the book and core


def test_a_duplicate_universe_row_is_fetched_once() -> None:
    # A code disclosed twice in one universe read (a re-fetch reaching the same as_of) earns one
    # per-fund fetch, not two: the tier-priority stream dedups, so its holdings/activity/valuation
    # legs — and its slice of the per-run deep-pull budget — are spent once, not doubled.
    feed = _RecordingFeed(
        universe=[_univ("DUP1"), _univ("DUP1")],  # the same code disclosed twice in one read
        scale=[_scale("DUP1", aum=68.0, amount=3.0)],
    )
    AShareUniverse().gather(Config(), as_of=AS_OF, fetched_at=FETCHED_AT, feed=feed, store=None)
    assert feed.activity_order.count("DUP1") == 1  # streamed once despite the duplicate row
