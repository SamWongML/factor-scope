"""The ingest transport seam — the committed-cassette replay that backs the offline ingest.

These pin :class:`CassetteFeed`: it replays the recorded responses under ``data/fixtures/cassettes``
into the same :class:`~factor_scope.store.Reading` rows the universe loop + price reconciliation
consume, honours the incremental ``since`` watermark on the per-fund series, and never touches the
network. ``get_feed`` selects the cassettes offline and the live adapters online.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pytest

from factor_scope.config import Config
from factor_scope.ingest import feed as feed_mod
from factor_scope.ingest import prices, trading_activity
from factor_scope.ingest.base import EASTMONEY_KLINE, _HostBreaker
from factor_scope.ingest.feed import CassetteFeed, LiveFeed, get_feed
from factor_scope.store import Reading

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"
FETCHED_AT = "2026-06-05T22:00:00Z"


@pytest.fixture(autouse=True)
def _isolate_breaker(monkeypatch) -> None:
    """The K-line host breaker is a run-scoped global; give each test a fresh one for isolation."""

    monkeypatch.setattr(feed_mod, "host_breaker", _HostBreaker())


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
    monkeypatch.setattr(
        "factor_scope.ingest.eastmoney.kline", lambda code, *, beg, impersonate="chrome": []
    )
    live = get_feed(Config(source="live", live_pacing_seconds=0.7), store=None)  # no store → cold
    assert isinstance(live, LiveFeed)
    live.activity("561010", fetched_at=FETCHED_AT)
    assert paced == [0.7]  # paced once with the configured delay, before the per-fund network call


def test_live_feed_threads_the_impersonation_profile_to_the_prices_leg(monkeypatch) -> None:
    # The EastMoney fingerprint is config-driven so it can be bumped when Chrome's TLS profile
    # drifts; get_feed must thread Config.eastmoney_impersonate down to the shared K-line client the
    # NAV leg rides (and only it — Baostock/Mootdx don't speak it).
    seen: dict[str, str] = {}

    def fake_kline(code, *, beg, impersonate="chrome"):
        seen["impersonate"] = impersonate
        return []

    monkeypatch.setattr("factor_scope.ingest.eastmoney.kline", fake_kline)
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
    # The activity leg rides the same K-line client as the NAV leg (one fetch feeds both), so
    # get_feed must thread Config.eastmoney_impersonate down to it too — else a bumped profile
    # fixes NAV but leaves activity hitting the same host with the stale one.
    seen: dict[str, str] = {}

    def fake_kline(code, *, beg, impersonate="chrome"):
        seen["impersonate"] = impersonate
        return []

    monkeypatch.setattr("factor_scope.ingest.eastmoney.kline", fake_kline)
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


# --- The store-aware load-shape seam: fake store + fake K-line client + fake spot board ---

RUN_AT = "2026-06-25T22:00:00Z"  # a Thursday, post-close
CLOSED = "2026-06-25"  # the expected closed trading session at the 22:00 run
SEEDED = "2024-01-02"  # before the ~650-day seed floor → a fund with this bar has its window filled


class _FakeStore:
    """A minimal point-in-time store for the seam: ``read_as_of`` honours the as-of ceiling.

    The feed reads the store twice per series — at the run date (the settled watermark) and at the
    seed floor (whether the span reaches back the window) — so the fake only needs ``read_as_of``.
    """

    def __init__(self, readings: list[Reading]) -> None:
        self._readings = readings

    def read_as_of(
        self, series: str, as_of: str, *, excluding: str | None = None
    ) -> list[Reading]:
        latest: dict[str, Reading] = {}
        for r in self._readings:
            if r.series != series or r.as_of > as_of:
                continue
            if excluding is not None and r.payload.get(excluding):
                continue  # skip flagged rows before the per-key collapse, like the real store
            if r.key not in latest or r.as_of > latest[r.key].as_of:
                latest[r.key] = r
        return list(latest.values())


def _hist(series: str, code: str, dates: list[str]) -> list[Reading]:
    """Settled history rows for one code in one series (only ``as_of`` and provenance are read)."""

    return [
        Reading(series=series, key=code, as_of=d, fetched_at=f"{d}T22:00:00Z", payload={"v": 1.0})
        for d in dates
    ]


def _provisional(series: str, code: str, date: str) -> Reading:
    """A provisional spot bar — a current-session estimate layered over settled history."""

    return Reading(
        series=series, key=code, as_of=date, fetched_at=f"{date}T22:00:00Z",
        payload={"v": 1.0, "provisional": True},
    )


def _warm(code: str, latest: str = "2026-06-24") -> list[Reading]:
    """Both legs settled, reaching the seed window and current — the spot-only steady state."""

    return _hist(prices.SERIES, code, [SEEDED, latest]) + _hist(
        trading_activity.SERIES, code, [SEEDED, latest]
    )


def _board(
    code: str = "561010", *, on: str = CLOSED, price: float = 0.918, turnover: float = 5.0
) -> dict[str, Any]:
    """A one-row normalised domain spot board — the snapshot the feed shares across its legs."""

    return {code: {"date": on, "nav": price, "turnover": turnover, "amount": 1_000_000.0}}


def _em_bar(d: str, *, close: float = 0.9, turnover: float = 4.0, amount: float = 2.0) -> dict:
    """A domain bar as the K-line client returns it — one call carries close + turnover/amount."""

    return {"date": d, "close": close, "turnover": turnover, "amount": amount}


def _fake_kline(
    monkeypatch,
    *,
    bars: list[dict] | None = None,
    error: Exception | None = None,
    captured: dict | None = None,
) -> None:
    """Mock the shared EastMoney K-line client and (optionally) record how it was called."""

    def kline(code: str, *, beg: str, impersonate: str = "chrome") -> list[dict]:
        if captured is not None:
            captured["calls"] = captured.get("calls", 0) + 1
            captured.update(code=code, beg=beg, impersonate=impersonate)
        if error is not None:
            raise error
        return bars or []

    monkeypatch.setattr("factor_scope.ingest.eastmoney.kline", kline)


def _forbid_kline(monkeypatch, captured: dict | None = None) -> None:
    """Mock the client to fail loudly if hit — for the warm and breaker-open paths."""

    _fake_kline(monkeypatch, error=AssertionError("push2his must not be hit"), captured=captured)


def _live(store: _FakeStore, board: dict[str, Any], **kwargs: Any) -> LiveFeed:
    feed = LiveFeed(store, **kwargs)  # type: ignore[arg-type]
    feed._spot = board  # pin the shared snapshot so the test never hits the network
    return feed


def test_steady_state_reads_the_spot_bar_settled_and_makes_no_kline_call(monkeypatch) -> None:
    # The headline: with full, current history both legs read the current bar off the shared board
    # and the run makes ~zero per-code push2his calls; the bar settles (advances the watermark).
    captured: dict = {}
    _forbid_kline(monkeypatch, captured)
    em = _live(_FakeStore(_warm("561010")), _board())._em("561010", fetched_at=RUN_AT)
    assert captured.get("calls", 0) == 0  # zero per-code history calls in steady state
    assert [r.as_of for r in em.nav] == [CLOSED] and em.nav[0].payload["nav"] == 0.918
    assert [r.as_of for r in em.activity] == [CLOSED] and em.activity[0].payload["turnover"] == 5.0
    assert all("provisional" not in r.payload for r in em.nav + em.activity)  # 数据日期 == closed


def test_cold_start_deep_pulls_one_kline_for_both_legs(monkeypatch) -> None:
    # No settled history → one K-line pull seeds the window, and the SAME bars feed both legs.
    captured: dict = {}
    _fake_kline(monkeypatch, bars=[_em_bar(CLOSED)], captured=captured)
    em = _live(_FakeStore([]), _board())._em("561010", fetched_at=RUN_AT)
    assert captured["calls"] == 1  # one push2his pull, not two
    assert captured["beg"] == prices._em_start(RUN_AT, None)  # the cold seed window
    assert em.nav[0].payload["nav"] == 0.9  # close → nav
    assert em.activity[0].payload == {"turnover": 4.0, "amount": 2.0}  # same call → activity


def test_an_intraday_deep_pull_tags_the_unsettled_current_bar_provisional(monkeypatch) -> None:
    # Off-nominal: a cold fund deep-pulled on an intraday (non-22:00) run. The K-line includes
    # today's still-forming bar, but the board's session date is still yesterday's settled close —
    # today has not settled. The deep path must tag the bar past the board's settled session
    # provisional, exactly as the spot leg does: else it records a non-final bar as settled and
    # advances the watermark past it, so tonight's post-close pull starts beyond the real close and
    # never backfills it. Bars up to the settled session stay settled.
    captured: dict = {}
    settled_session = "2026-06-24"  # the board's last settled close; today (CLOSED) has not settled
    _fake_kline(monkeypatch, bars=[_em_bar(settled_session), _em_bar(CLOSED)], captured=captured)
    em = _live(_FakeStore([]), _board(on=settled_session))._em("561010", fetched_at=RUN_AT)
    assert captured["calls"] == 1
    settled = [r.as_of for r in em.nav if "provisional" not in r.payload]
    provisional = [r.as_of for r in em.nav if r.payload.get("provisional")]
    assert settled == [settled_session]  # the settled close is kept settled — it advances the floor
    assert provisional == [CLOSED]  # the current bar past the board → provisional, floor skips it
    assert [r.as_of for r in em.activity if r.payload.get("provisional")] == [CLOSED]  # both legs


def test_short_span_re_seeds_via_a_deep_pull(monkeypatch) -> None:
    # History present and current, but none reaching back the seed window → cold, re-seeded
    # from the window floor (so the 200-day-MA gate isn't blind), not just incrementally topped up.
    captured: dict = {}
    _fake_kline(monkeypatch, bars=[_em_bar(CLOSED)], captured=captured)
    recent = _hist(prices.SERIES, "561010", ["2026-06-20", "2026-06-24"]) + _hist(
        trading_activity.SERIES, "561010", ["2026-06-20", "2026-06-24"]
    )
    _live(_FakeStore(recent), _board())._em("561010", fetched_at=RUN_AT)
    assert captured["calls"] == 1
    assert captured["beg"] == prices._em_start(RUN_AT, None)  # seed floor (re-seed), not watermark


def test_short_span_re_seed_keeps_the_early_history_it_backfills(monkeypatch) -> None:
    # A re-seed pulls the full window precisely to extend the span back to the seed floor, so the
    # early bars it returns (older than the current watermark) must be KEPT, not clipped at the
    # watermark — clipping them would discard the backfill, leaving the span short and the fund
    # accruing one bar a night (the slow path the seed window exists to avoid), gate still blind.
    early = "2025-06-01"  # within the re-seed window, but older than the current watermark
    _fake_kline(monkeypatch, bars=[_em_bar(early), _em_bar(CLOSED)])
    short = _hist(prices.SERIES, "561010", ["2026-06-20", "2026-06-24"]) + _hist(
        trading_activity.SERIES, "561010", ["2026-06-20", "2026-06-24"]
    )
    em = _live(_FakeStore(short), _board())._em("561010", fetched_at=RUN_AT)
    assert early in [r.as_of for r in em.nav]  # the backfilled NAV bar survives the floor
    assert early in [r.as_of for r in em.activity]  # and the same on the shared activity leg


def test_gap_deep_pulls_incrementally_from_the_watermark(monkeypatch) -> None:
    # Seeded but the latest settled bar is weeks behind the closed session → a gap pull
    # backfilling from the watermark (day-after), not the seed window — recovery stays cheap.
    captured: dict = {}
    _fake_kline(monkeypatch, bars=[_em_bar(CLOSED)], captured=captured)
    gapped = _hist(prices.SERIES, "561010", [SEEDED, "2026-06-05"]) + _hist(
        trading_activity.SERIES, "561010", [SEEDED, "2026-06-05"]
    )
    _live(_FakeStore(gapped), _board())._em("561010", fetched_at=RUN_AT)
    assert captured["calls"] == 1
    assert captured["beg"] == "20260606"  # the day after the watermark, not the seed floor


def test_a_recent_fund_within_the_gap_tolerance_stays_on_the_spot_board(monkeypatch) -> None:
    # One session behind (steady state — today not yet recorded) is within gap_sessions, so
    # the fund stays on the cheap board rather than deep-pulling every night.
    captured: dict = {}
    _forbid_kline(monkeypatch, captured)
    em = _live(_FakeStore(_warm("561010", latest="2026-06-24")), _board())._em(
        "561010", fetched_at=RUN_AT
    )
    assert captured.get("calls", 0) == 0
    assert em.nav and em.activity  # served from the board


def test_a_holiday_cluster_keeps_a_warm_fund_on_the_spot_board(monkeypatch) -> None:
    # During an A-share holiday cluster (Spring Festival / Golden Week) the wall clock runs several
    # weekdays past the last trading session, but the spot board's session date does NOT advance —
    # there are simply no new sessions. The gap measure must ride the board's session date, not the
    # wall clock: else every warm fund reads as >gap_sessions behind and fires a per-fund push2his
    # that returns nothing — the steady-state "~zero per-code calls" guarantee evaporating across
    # the whole universe for every holiday night, the exact burst #106 removes.
    captured: dict = {}
    _forbid_kline(monkeypatch, captured)
    last_session = "2026-02-13"  # the Friday before the week-long Spring Festival closure
    holiday_run = "2026-02-20T22:00:00Z"  # the following Friday — 5 weekday-sessions of wall clock
    feed = _live(_FakeStore(_warm("561010", latest=last_session)), _board(on=last_session))
    em = feed._em("561010", fetched_at=holiday_run)
    assert captured.get("calls", 0) == 0  # no deep pull despite a >gap_sessions wall-clock gap
    assert em.nav == [] and em.activity == []  # board hasn't advanced past the watermark → no write


def test_spot_bar_is_provisional_when_the_board_date_is_not_the_closed_session(monkeypatch) -> None:
    # A stale/intraday board (数据日期 != the closed session) is the current estimate, not settled
    # history: both legs are tagged provisional so the floor skips them and a later pull backfills.
    _forbid_kline(monkeypatch)
    feed = _live(_FakeStore(_warm("561010", latest="2026-06-23")), _board(on="2026-06-24"))
    em = feed._em("561010", fetched_at=RUN_AT)
    assert em.nav[0].as_of == "2026-06-24" and em.nav[0].payload["provisional"] is True
    assert em.activity[0].payload["provisional"] is True


def test_a_provisional_bar_does_not_mask_the_settled_watermark_into_a_re_seed(monkeypatch) -> None:
    # Last night the board was intraday, so a provisional spot bar sits on top of settled history.
    # The settled bar beneath must still set the watermark — else it reads as no history and tonight
    # forces a full cold re-seed of the whole window instead of staying on the cheap spot board.
    captured: dict = {}
    _forbid_kline(monkeypatch, captured)
    settled = _hist(prices.SERIES, "561010", [SEEDED, "2026-06-23"]) + _hist(
        trading_activity.SERIES, "561010", [SEEDED, "2026-06-23"]
    )
    masking = [
        _provisional(prices.SERIES, "561010", "2026-06-24"),
        _provisional(trading_activity.SERIES, "561010", "2026-06-24"),
    ]
    em = _live(_FakeStore(settled + masking), _board())._em("561010", fetched_at=RUN_AT)
    assert captured.get("calls", 0) == 0  # settled watermark seen → warm, no per-code re-seed
    assert em.nav and em.activity  # served from the shared spot board


def test_one_kline_call_feeds_both_legs_across_the_universe_and_price_loops(monkeypatch) -> None:
    # The activity (universe loop) and NAV (price loop) legs share ONE memoised fetch, so a
    # fund costs a single push2his request even though two separate loops consume it.
    captured: dict = {}
    _fake_kline(monkeypatch, bars=[_em_bar(CLOSED)], captured=captured)
    for leg in ("baostock", "mootdx"):
        monkeypatch.setattr(
            f"factor_scope.ingest.{leg}.fetch_live",
            lambda code, *, fetched_at, since=None: [],
        )
    feed = _live(_FakeStore([]), _board(), pace_seconds=0)
    act = feed.activity("561010", fetched_at=RUN_AT)  # universe loop
    nav = feed.price_sources("561010", fetched_at=RUN_AT)[0]  # price loop reuses the memo
    assert captured["calls"] == 1  # one shared pull, not one per leg
    assert act and nav  # both legs got readings from the single fetch


def test_a_deep_pull_failure_backs_nav_onto_sina_and_activity_onto_the_spot_board(
    monkeypatch, caplog
):
    # On a push2his refusal the breaker records, the NAV leg falls to Sina's settled close, and the
    # activity leg to the current (provisional) spot bar — a block degrades, not drops. The
    # degradation must also log loudly, naming the blocked code: a silent fallback could hide a host
    # blocked for weeks behind today's bar — the exact failure mode CLAUDE.md's guardrails forbid.
    _fake_kline(monkeypatch, error=ConnectionError("push2his reset"))
    monkeypatch.setattr(
        "factor_scope.ingest.prices.sina",
        lambda code, *, fetched_at, floor: [
            Reading(series=prices.SERIES, key=code, as_of=CLOSED, fetched_at=fetched_at,
                    payload={"nav": 0.7, "source": "akshare"})
        ],
    )
    breaker = _HostBreaker(threshold=5)
    monkeypatch.setattr(feed_mod, "host_breaker", breaker)
    with caplog.at_level(logging.WARNING, logger="factor_scope.ingest.feed"):
        em = _live(_FakeStore([]), _board())._em("561010", fetched_at=RUN_AT)
    assert breaker.failures(EASTMONEY_KLINE) == 1  # the refusal counts toward tripping the breaker
    assert em.nav[0].payload["nav"] == 0.7  # NAV backed onto Sina
    assert em.activity[0].payload["provisional"] is True  # activity fell to the current spot bar
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("561010" in msg for msg in warned)  # the blocked code is named in the warning


def test_an_open_breaker_skips_the_kline_and_uses_the_fallbacks(monkeypatch) -> None:
    # Once the shared host has tripped open, a deep code goes straight to the fallbacks for the rest
    # of the run rather than re-hitting a blocking IP per fund.
    captured: dict = {}
    _forbid_kline(monkeypatch, captured)
    monkeypatch.setattr("factor_scope.ingest.prices.sina", lambda code, *, fetched_at, floor: [])
    breaker = _HostBreaker(threshold=1)
    breaker.record_failure(EASTMONEY_KLINE)  # tripped open
    monkeypatch.setattr(feed_mod, "host_breaker", breaker)
    em = _live(_FakeStore([]), _board())._em("561010", fetched_at=RUN_AT)
    assert captured.get("calls", 0) == 0  # the host was left untouched
    assert em.activity[0].payload["provisional"] is True  # served by the spot board


def test_a_warm_fund_absent_from_the_board_degrades_to_no_reading(monkeypatch) -> None:
    # A delisted/absent code on the warm path isn't on the board → both surfaces degrade to no
    # reading (the factors fall to invalid), never a crash.
    _forbid_kline(monkeypatch)
    em = _live(_FakeStore(_warm("561010")), {})._em("561010", fetched_at=RUN_AT)
    assert em.nav == [] and em.activity == []


def test_sessions_between_counts_trading_weekdays_not_calendar_days() -> None:
    # The gap measure gating spot-vs-deep must count Mon–Fri sessions, not calendar days: a fund
    # stored Friday and checked Monday is ONE session behind (within the default tolerance, so it
    # stays on the cheap board), not three — else the weekend alone would deep-pull every fund each
    # Monday, the push2his burst the load-shape removes. (RUN_AT's 2026-06-25 is a Thursday.)
    sessions_between = feed_mod._sessions_between
    assert sessions_between(date(2026, 6, 26), date(2026, 6, 29)) == 1  # Fri → Mon: weekend skipped
    assert sessions_between(date(2026, 6, 22), date(2026, 6, 23)) == 1  # Mon → Tue: one session
    assert sessions_between(date(2026, 6, 19), date(2026, 6, 26)) == 5  # Fri → next Fri: a week
    assert sessions_between(date(2026, 6, 25), date(2026, 6, 25)) == 0  # already current
    assert sessions_between(date(2026, 6, 25), date(2026, 6, 24)) == 0  # skew: closed < latest


def test_asymmetric_load_shape_floors_a_warm_leg_while_seeding_a_cold_one(monkeypatch) -> None:
    # A fund can be warm on one leg and cold on the other (e.g. NAV history intact, activity history
    # lost). The single shared K-line pull must floor EACH leg independently: the warm NAV leg keeps
    # only bars past its watermark while the cold activity leg seeds the whole window — one fetch,
    # two floors, no double-write on the warm leg and no dropped backfill on the cold one.
    early = "2025-06-01"  # within the seed window, but older than the warm NAV watermark
    captured: dict = {}
    _fake_kline(monkeypatch, bars=[_em_bar(early), _em_bar(CLOSED)], captured=captured)
    warm_nav_only = _hist(prices.SERIES, "561010", [SEEDED, "2026-06-24"])  # no activity history
    em = _live(_FakeStore(warm_nav_only), _board())._em("561010", fetched_at=RUN_AT)
    assert captured["calls"] == 1  # one shared pull feeds both legs despite their differing shapes
    assert captured["beg"] == prices._em_start(RUN_AT, None)  # the cold (activity) leg's seed floor
    assert [r.as_of for r in em.nav] == [CLOSED]  # warm NAV floored at its watermark — no re-write
    assert [r.as_of for r in em.activity] == [early, CLOSED]  # cold activity seeded from the floor


def test_a_feed_reused_across_run_stamps_fails_loudly_instead_of_returning_stale_reads(
    monkeypatch,
) -> None:
    # The per-code K-line memo, the per-series settled-watermark map, and the per-series seeded set
    # are keyed on code/series alone — correct only because one feed serves exactly one run at one
    # stamp (the snapshot boundary). Reusing a feed across stamps would silently hand back the first
    # run's readings/watermarks; binding the feed to its first stamp turns that latent staleness
    # into a loud failure — even for an already-memoised code, the path that would go stale.
    _forbid_kline(monkeypatch)
    feed = _live(_FakeStore(_warm("561010")), _board())
    feed._em("561010", fetched_at=RUN_AT)  # binds the feed to this run's stamp (memoising 561010)
    with pytest.raises(RuntimeError, match="single-run"):
        feed._em("561010", fetched_at="2026-06-26T22:00:00Z")  # a second run's stamp → loud failure
