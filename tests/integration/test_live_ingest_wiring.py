"""The live ingest path pulls the full fund universe + refreshes its holdings, not just prices.

These stay offline by stubbing each adapter's heavy ``fetch_live`` (the real bodies hit the
network and live behind ``FACTOR_SCOPE_LIVE=1`` in ``test_adapters_live.py``). They pin the wiring
of the A-share market's live gather: the whole fund universe + ETF scale are pulled, every
on-exchange ETF's holdings are refreshed (so the connection graph rebuilds from live disclosures),
and each configured EDGAR filer is pulled.
"""

import logging
import time

import pytest

from factor_scope import ingest
from factor_scope.config import Config
from factor_scope.ingest import (
    baostock,
    demand,
    edgar,
    etf_scale,
    fred,
    fund_holdings,
    fund_universe,
    fundamentals,
    mootdx,
    prices,
    trading_activity,
)
from factor_scope.ingest.base import EASTMONEY_KLINE, IngestError, host_breaker
from factor_scope.markets.ashare import AShareMarket
from factor_scope.store import Reading

pytestmark = pytest.mark.integration

# A small live universe stand-in: two on-exchange ETFs (whose holdings the graph needs) + one
# off-exchange fund (no holdings refresh). The held positions fixture is priced independently.
_UNIVERSE = (("561010", True), ("588200", True), ("000001", False))


def _stub_adapters(monkeypatch) -> None:
    # A fully-configured live host with the network stubbed out: the credential preflight (run by
    # pipeline.ingest) needs the required keys present even though the feeds themselves are faked.
    monkeypatch.setenv("FRED_API_KEY", "stub-key")
    monkeypatch.setattr(
        prices,
        "fetch_live",
        lambda key, *, fetched_at, since=None, impersonate="chrome": [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.0})
        ],
    )
    monkeypatch.setattr(
        baostock,
        "fetch_live",
        lambda key, *, fetched_at, since=None: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.0})  # corroborates the AkShare read
        ],
    )
    monkeypatch.setattr(
        mootdx,
        "fetch_live",
        lambda key, *, fetched_at, since=None: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.0})  # the third source also corroborates
        ],
    )
    monkeypatch.setattr(
        fund_holdings,
        "fetch_live",
        lambda fund, *, fetched_at, since=None: [
            Reading(series="fund_holdings", key=f"{fund}/X", as_of="2026-03-31",
                    fetched_at=fetched_at, payload={"fund": fund, "holding": "X", "weight": 0.1})
        ],
    )
    monkeypatch.setattr(
        edgar,
        "fetch_live",
        lambda cik, *, form="13F-HR", fetched_at: [
            Reading(series="edgar", key=f"{cik}/COHR", as_of="2026-03-31", fetched_at=fetched_at,
                    payload={"filer": cik, "holding": "COHR", "weight": 0.05, "form": form})
        ],
    )
    monkeypatch.setattr(fred, "fetch_live", lambda series_id, *, fetched_at: [])
    # One shared spot board per run; the three legs below take it but ignore its contents (they
    # return canned readings), so a bare snapshot is enough to thread through the gather.
    monkeypatch.setattr(etf_scale, "fetch_spot_board", lambda: {code: {} for code, _ in _UNIVERSE})
    monkeypatch.setattr(
        fund_universe,
        "fetch_live",
        lambda board, *, as_of, fetched_at: [
            Reading(series="fund_universe", key=code, as_of=as_of, fetched_at=fetched_at,
                    payload={"name": code, "type": "ETF", "on_exchange": on_exchange,
                             "inception": "2021-01-20", "delisting": "", "fee": None,
                             "tracking_error": None, "top10_weight": None, "valid": False})
            for code, on_exchange in _UNIVERSE
        ],
    )
    monkeypatch.setattr(
        etf_scale,
        "fetch_live",
        lambda board, *, fetched_at: [
            Reading(series="etf_scale", key="561010", as_of="2026-05-31", fetched_at=fetched_at,
                    payload={"exchange": "sse", "aum": 68.0, "shares": 40.0})
        ],
    )
    monkeypatch.setattr(
        trading_activity,
        "fetch_live",
        lambda board, code, *, fetched_at, since=None: [
            Reading(series="trading_activity", key=code, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"turnover": 3.1, "amount": 2.8})
        ],
    )
    monkeypatch.setattr(
        fundamentals,
        "fetch_live",
        lambda code, *, fetched_at, since=None: [
            Reading(series="fundamentals", key=code, as_of="2026-05-29", fetched_at=fetched_at,
                    payload={"pe": 42.5})
        ],
    )
    monkeypatch.setattr(
        demand,
        "fetch_live",
        lambda *, fetched_at: [
            Reading(series="demand", key=demand.KEY, as_of="2026-03-31", fetched_at=fetched_at,
                    payload={"revision": 0.08})
        ],
    )


def test_gather_live_pulls_the_full_universe_and_etf_scale(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")
    universe = {r.key for r in readings if r.series == "fund_universe"}
    scale = [r for r in readings if r.series == "etf_scale"]
    assert universe == {code for code, _ in _UNIVERSE}  # the whole fund universe, not just the book
    assert scale and scale[0].payload["aum"] == 68.0


def test_gather_live_constructs_exactly_one_feed_for_the_whole_run(monkeypatch) -> None:
    # One store-aware feed is built per run and threaded through the universe + price legs, so
    # `get_feed` runs once for the whole gather, not once per gather method.
    from factor_scope.markets import ashare

    _stub_adapters(monkeypatch)
    real_get_feed = ashare.get_feed
    calls = {"n": 0}

    def counting_get_feed(config, store):
        calls["n"] += 1
        return real_get_feed(config, store)

    monkeypatch.setattr(ashare, "get_feed", counting_get_feed)
    AShareMarket().gather(Config(source="live"), as_of="2026-06-05")
    assert calls["n"] == 1  # constructed once for the whole gather, then passed down


def test_gather_live_fetches_the_spot_board_once_and_shares_it(monkeypatch) -> None:
    # The whole-market spot board is a single shared per-run snapshot: pulled once, then handed to
    # the universe-membership, ETF-scale, and trading-activity-fallback legs — not fetched per leg.
    _stub_adapters(monkeypatch)
    board = {"561010": object()}  # a sentinel snapshot — the legs only need its identity here
    pulls = {"n": 0}
    seen: list[object] = []

    def fetch_board_once():
        pulls["n"] += 1
        return board

    def universe(b, *, as_of, fetched_at):
        seen.append(b)
        return [
            Reading(series="fund_universe", key=code, as_of=as_of, fetched_at=fetched_at,
                    payload={"name": code, "type": "ETF", "on_exchange": on_exchange,
                             "inception": "2021-01-20", "delisting": "", "fee": None,
                             "tracking_error": None, "top10_weight": None, "valid": False})
            for code, on_exchange in _UNIVERSE
        ]

    def scale(b, *, fetched_at):
        seen.append(b)
        return [Reading(series="etf_scale", key="561010", as_of="2026-05-31", fetched_at=fetched_at,
                        payload={"exchange": "sse", "aum": 68.0, "shares": 40.0})]

    def activity(b, code, *, fetched_at, since=None):
        seen.append(b)
        return [Reading(series="trading_activity", key=code, as_of="2026-06-05",
                        fetched_at=fetched_at, payload={"turnover": 3.1, "amount": 2.8})]

    monkeypatch.setattr(etf_scale, "fetch_spot_board", fetch_board_once)
    monkeypatch.setattr(fund_universe, "fetch_live", universe)
    monkeypatch.setattr(etf_scale, "fetch_live", scale)
    monkeypatch.setattr(trading_activity, "fetch_live", activity)

    AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    assert pulls["n"] == 1  # pulled exactly once for the whole run, not once per leg
    # one board reached every leg that reads it — universe, scale, and the activity leg of each
    # on-exchange ETF (561010 + 588200, both non-dead) — so exactly four consumers, same snapshot
    assert len(seen) == 4 and all(b is board for b in seen)


def test_gather_live_refreshes_holdings_for_each_on_exchange_etf(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    on_exchange = {code for code, on_exchange in _UNIVERSE if on_exchange}
    refreshed = {r.payload["fund"] for r in readings if r.series == "fund_holdings"}
    # one live holdings refresh per on-exchange ETF (the off-exchange fund discloses none) — so the
    # graph rebuilds from the universe's live disclosures, not just the held book.
    assert refreshed == on_exchange
    # the crowding surface (turnover + traded value) is pulled for the same on-exchange ETFs
    activity = {r.key for r in readings if r.series == "trading_activity"}
    assert activity == on_exchange
    # …as is each ETF's valuation history (the valuation factor's PE surface)
    valued = {r.key for r in readings if r.series == "fundamentals"}
    assert valued == on_exchange
    # and the book-wide end-demand dial is pulled once for the whole run
    assert [r for r in readings if r.series == "demand"]


def test_ingest_discloses_a_fund_the_live_feed_dropped(monkeypatch, tmp_path) -> None:
    # AkShare has no fund-delisting feed: a dead fund simply vanishes from the next pull. Two
    # nightly ingests on a durable store, with the off-exchange fund gone on night two → the store
    # discloses it delisted as of night two, while the night-one read still shows it alive
    # (point-in-time: a later disclosure never rewrites what was knowable earlier).
    from factor_scope.pipeline import ingest as nightly_ingest
    from factor_scope.store import DuckDBStore

    _stub_adapters(monkeypatch)
    paths = {"store_path": tmp_path / "store.duckdb", "graph_path": tmp_path / "graph.duckdb"}
    nightly_ingest(Config(source="live", as_of="2026-06-05", **paths))
    monkeypatch.setattr(
        fund_universe,
        "fetch_live",
        lambda board, *, as_of, fetched_at: [
            Reading(series="fund_universe", key=code, as_of=as_of, fetched_at=fetched_at,
                    payload={"name": code, "type": "ETF", "on_exchange": on_exchange,
                             "inception": "2021-01-20", "delisting": "", "fee": None,
                             "tracking_error": None, "top10_weight": None, "valid": False})
            for code, on_exchange in _UNIVERSE
            if code != "000001"  # the off-exchange fund vanished from the feed overnight
        ],
    )
    nightly_ingest(Config(source="live", as_of="2026-06-06", **paths))

    store = DuckDBStore(tmp_path / "store.duckdb")
    try:
        night_two = {r.key: r for r in store.read_as_of("fund_universe", "2026-06-06")}
        night_one = {r.key: r for r in store.read_as_of("fund_universe", "2026-06-05")}
    finally:
        store.close()
    assert night_two["000001"].payload["delisting"] == "2026-06-06"
    assert night_one["000001"].payload["delisting"] == ""  # alive at the old as_of (survivorship)
    assert night_two["561010"].payload["delisting"] == ""  # the refreshed funds are untouched


def test_gather_live_trims_dead_funds_but_fetches_core_and_probation(monkeypatch) -> None:
    # The universe tier caps the per-fund burst that trips the history host: a seasoned, sub-floor,
    # untraded zombie is recorded in the universe/scale reads but never deep-fetched, while core and
    # the uncrowded probation funds (the discovery candidates) still earn their per-fund legs.
    _stub_adapters(monkeypatch)
    tiered = (("561010", True), ("159001", True), ("159002", True))  # core, probation, dead
    monkeypatch.setattr(
        fund_universe,
        "fetch_live",
        lambda board, *, as_of, fetched_at: [
            Reading(series="fund_universe", key=code, as_of=as_of, fetched_at=fetched_at,
                    payload={"name": code, "type": "ETF", "on_exchange": on_ex,
                             "inception": "2021-01-20", "delisting": "", "fee": None,
                             "tracking_error": None, "top10_weight": None, "valid": False})
            for code, on_ex in tiered
        ],
    )
    monkeypatch.setattr(
        etf_scale,
        "fetch_live",
        lambda board, *, fetched_at: [
            Reading(series="etf_scale", key="561010", as_of="2026-05-31", fetched_at=fetched_at,
                    payload={"exchange": "sse", "aum": 68.0, "shares": 40.0, "amount": 3.0}),
            Reading(series="etf_scale", key="159001", as_of="2026-05-31", fetched_at=fetched_at,
                    payload={"exchange": "szse", "aum": 3.0, "shares": 2.0, "amount": 1.0}),
            Reading(series="etf_scale", key="159002", as_of="2026-05-31", fetched_at=fetched_at,
                    payload={"exchange": "szse", "aum": 0.2, "shares": 0.1, "amount": 0.0}),
        ],
    )
    readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    activity = {r.key for r in readings if r.series == "trading_activity"}
    assert activity == {"561010", "159001"}  # the dead 159002 trimmed; discovery candidates kept
    priced = {r.key for r in readings if r.series == "prices"}
    assert "159002" not in priced  # nor does the zombie pay the deep-price (push2his) pull
    assert {"561010", "159001"} <= priced


def test_gather_live_pulls_each_configured_edgar_filer(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    config = Config(source="live", edgar_ciks=("0001067983", "0000102909"))
    readings = AShareMarket().gather(config, as_of="2026-06-05")

    edgar_rows = [r for r in readings if r.series == "edgar"]
    assert {r.payload["filer"] for r in edgar_rows} == {"0001067983", "0000102909"}
    # pulled as monthly N-PORT and weighted, so the holdings feed the look-through graph
    assert all(r.payload["form"] == "NPORT-P" for r in edgar_rows)
    assert all("weight" in r.payload for r in edgar_rows)


def test_gather_live_pulls_no_edgar_filers_by_default(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")
    assert not [r for r in readings if r.series == "edgar"]


def test_gather_resets_the_host_breaker_at_the_start_of_each_run(monkeypatch) -> None:
    # The breaker is run-scoped: a previous night's EastMoney block must not leak into tonight and
    # make every fund skip the host. Each gather resets it at the top.
    _stub_adapters(monkeypatch)
    for _ in range(10):
        host_breaker.record_failure(EASTMONEY_KLINE)  # a prior run left it tripped open
    assert host_breaker.is_open(EASTMONEY_KLINE)
    AShareMarket().gather(Config(source="live"), as_of="2026-06-05")
    assert not host_breaker.is_open(EASTMONEY_KLINE)  # this run reset it before pulling


def test_live_gather_stamps_a_real_fetched_at_not_the_fixtures_derived_one(monkeypatch) -> None:
    # `fetched_at` is "when we pulled it" — telemetry, never the artifact's clock. Fixtures derive
    # it from as_of (deterministic); a live pull stamps the real wall-clock instant of the pull.
    _stub_adapters(monkeypatch)
    monkeypatch.setattr(
        "factor_scope.markets.ashare.fetched_at_now", lambda: "2026-06-05T13:45:00Z"
    )
    readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    pulled = [r for r in readings if r.series == "prices"]
    assert pulled and all(r.fetched_at == "2026-06-05T13:45:00Z" for r in pulled)
    # not the fixtures-derived "{as_of}T22:00:00Z" stamp
    assert all(r.fetched_at != "2026-06-05T22:00:00Z" for r in readings)


def test_live_ingest_warns_when_a_reading_is_dated_after_the_run(monkeypatch, tmp_path, caplog):
    # Clock/TZ-skew safety net: a feed reading dated past the run's as_of is kept (append-only) but
    # excluded by the point-in-time ceiling, and surfaced as a warning — never silently dropped.
    from factor_scope.pipeline import ingest as nightly_ingest
    from factor_scope.store import DuckDBStore

    _stub_adapters(monkeypatch)
    monkeypatch.setattr(
        demand,
        "fetch_live",
        lambda *, fetched_at: [
            Reading(series="demand", key=demand.KEY, as_of="2026-06-07", fetched_at=fetched_at,
                    payload={"revision": 0.08})  # dated two days AFTER the run
        ],
    )
    paths = {"store_path": tmp_path / "s.duckdb", "graph_path": tmp_path / "g.ladybug"}
    with caplog.at_level(logging.WARNING, logger="factor_scope.pipeline"):
        nightly_ingest(Config(source="live", as_of="2026-06-05", **paths))
    assert any("dated after as_of 2026-06-05" in m for m in caplog.messages)

    store = DuckDBStore(tmp_path / "s.duckdb")
    try:
        # The ceiling excludes the future-dated row from tonight's reasoning…
        assert store.read_as_of("demand", "2026-06-05") == []
        # …but it is retained (append-only) and surfaces once a later night's as_of reaches it.
        assert any(r.as_of == "2026-06-07" for r in store.history("demand"))
    finally:
        store.close()


def test_a_same_day_live_re_ingest_keeps_the_snapshot_stable(monkeypatch, tmp_path) -> None:
    # snapshot_id hashes fetched_at, so a real wall-clock stamp could perturb it. The content-
    # addressed dedup means a same-day re-pull of unchanged facts writes nothing, so the snapshot —
    # fetched_at included — is stable across reruns even though each pull reads a different clock.
    from factor_scope.pipeline import ingest as nightly_ingest
    from factor_scope.store import DuckDBStore

    _stub_adapters(monkeypatch)
    clock = iter(["2026-06-05T13:00:00Z", "2026-06-05T19:30:00Z"]).__next__
    monkeypatch.setattr("factor_scope.markets.ashare.fetched_at_now", clock)
    paths = {"store_path": tmp_path / "s.duckdb", "graph_path": tmp_path / "g.ladybug"}

    def _snapshot() -> str:
        store = DuckDBStore(tmp_path / "s.duckdb")
        try:
            return store.snapshot_id("2026-06-05")
        finally:
            store.close()

    nightly_ingest(Config(source="live", as_of="2026-06-05", **paths))
    first = _snapshot()
    nightly_ingest(Config(source="live", as_of="2026-06-05", **paths))  # re-pull, later wall clock
    # unchanged facts re-pulled → dedup no-op → fetched_at not re-stamped, so the snapshot holds
    assert _snapshot() == first


def test_gather_live_corroborates_prices_across_sources(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    # AkShare and Baostock agree → one corroborated price per held fund (not one per source)
    readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")
    held = {r.key for r in readings if r.series == "positions"}
    priced = [r for r in readings if r.series == "prices"]
    assert {r.key for r in priced} == held
    assert len(priced) == len(held)


def test_gather_live_falls_back_to_baostock_when_akshare_is_down(monkeypatch, caplog) -> None:
    _stub_adapters(monkeypatch)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)  # don't really back off in the test

    def _akshare_down(key, *, fetched_at, since=None, impersonate="chrome"):
        raise RuntimeError("AkShare IP-blocked")

    monkeypatch.setattr(prices, "fetch_live", _akshare_down)
    for source in (baostock, mootdx):  # the two surviving sources agree on the substitute NAV
        monkeypatch.setattr(
            source,
            "fetch_live",
            lambda key, *, fetched_at, since=None: [
                Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                        payload={"nav": 2.0})
            ],
        )
    # AkShare offline must not kill the run — the other sources substitute for it (failover).
    with caplog.at_level(logging.WARNING):
        readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")
    held = {r.key for r in readings if r.series == "positions"}
    priced = [r for r in readings if r.series == "prices"]
    assert {r.key for r in priced} == held
    assert all(r.payload["nav"] == 2.0 for r in priced)  # the substituted Baostock NAV
    # the failover is logged, not silent — silent degradation is the failure mode we guard against
    assert any("akshare" in rec.message.lower() for rec in caplog.records)


def test_gather_live_flags_a_source_disagreement_and_continues(monkeypatch, caplog) -> None:
    _stub_adapters(monkeypatch)
    # ONE fund disagrees (an isolated tick), the rest corroborate → flag it and CONTINUE the run.
    monkeypatch.setattr(
        baostock,
        "fetch_live",
        lambda key, *, fetched_at, since=None: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 99.0 if key == "561010" else 1.0})
        ],
    )
    with caplog.at_level(logging.WARNING):
        readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")
    priced = {r.key: r for r in readings if r.series == "prices"}
    assert priced  # the run completed despite the disagreement
    assert priced["561010"].payload["nav"] == 1.0  # primary AkShare value retained
    assert priced["561010"].payload["divergence"] == 99.0  # the peer NAV, flagged for review
    assert "divergence" not in priced["515880"].payload  # the corroborating funds are untouched
    assert any("degraded" in rec.message.lower() for rec in caplog.records)  # surfaced, not silent


def test_gather_live_trips_circuit_breaker_on_systemic_divergence(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    # EVERY fund disagrees → not an isolated tick but a systemic break (e.g. a source switched to
    # adjusted prices). Fail the whole run loudly rather than ship a wall of unreconciled NAVs.
    monkeypatch.setattr(
        baostock,
        "fetch_live",
        lambda key, *, fetched_at, since=None: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 99.0})
        ],
    )
    with pytest.raises(IngestError, match="unreconciled"):
        AShareMarket().gather(Config(source="live"), as_of="2026-06-05")


def test_gather_live_respects_configured_tolerance(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    # A 2% gap on every fund would flag (and trip the breaker) at the 0.5% default; a loosened
    # config tolerance must be honoured instead — proving the band is config-driven, not hard-coded.
    monkeypatch.setattr(
        baostock,
        "fetch_live",
        lambda key, *, fetched_at, since=None: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.02})
        ],
    )
    config = Config(source="live", corroboration_tolerance=0.05)
    readings = AShareMarket().gather(config, as_of="2026-06-05")
    priced = [r for r in readings if r.series == "prices"]
    assert priced and not any("divergence" in r.payload for r in priced)  # 2% within the 5% band


def test_with_retries_backs_off_with_full_jitter_then_succeeds(monkeypatch) -> None:
    # A transient blip (IP throttle) should be retried, not surfaced — with exponential backoff and
    # full jitter (sleep in [0, base·2^n]); pin the upper bound to make the schedule deterministic.
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    monkeypatch.setattr("random.uniform", lambda _lo, hi: hi)  # full-jitter ceiling
    attempts = {"n": 0}

    def flaky() -> list[str]:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("throttled")
        return ["ok"]

    assert ingest._with_retries(flaky) == ["ok"]
    assert attempts["n"] == 3
    assert sleeps == [1.0, 2.0]  # base·2^0, base·2^1 — exponential, then it succeeded


def test_with_retries_gives_up_after_max_attempts(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    def always_down() -> list[str]:
        raise RuntimeError("IP-blocked")

    with pytest.raises(RuntimeError, match="IP-blocked"):
        ingest._with_retries(always_down)


def test_with_timeout_returns_a_fast_result() -> None:
    assert ingest._with_timeout(lambda: ["ok"], 1.0) == ["ok"]


def test_with_timeout_abandons_a_hung_call() -> None:
    # A blocking source read that exposes no timeout must be bounded by an outer deadline; the
    # worker is abandoned (daemon) and a TimeoutError is raised rather than stalling the run.
    with pytest.raises(TimeoutError):
        ingest._with_timeout(lambda: time.sleep(0.5) or ["never"], 0.02)


def test_with_timeout_propagates_the_workers_error() -> None:
    def boom() -> list[str]:
        raise RuntimeError("source blew up")

    with pytest.raises(RuntimeError, match="blew up"):
        ingest._with_timeout(boom, 1.0)


def test_live_or_empty_abandons_a_hung_read_and_falls_back(monkeypatch, caplog) -> None:
    # A source that hangs past the deadline on every attempt is logged and yields no rows, so the
    # cross-source can substitute — a hung scraper must not stall the nightly run.
    monkeypatch.setattr(ingest, "_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr("random.uniform", lambda _lo, _hi: 0.0)  # no real backoff delay

    def hung(code, *, fetched_at):
        time.sleep(0.5)  # exceeds the deadline on every attempt
        raise AssertionError("should have been abandoned")

    with caplog.at_level(logging.WARNING):
        out = ingest._live_or_empty(hung, "561010", source="akshare", fetched_at="t")
    assert out == []
    assert any("akshare" in rec.message.lower() for rec in caplog.records)


def test_live_or_empty_forwards_extra_kwargs_to_the_fetch() -> None:
    # The per-fund factor legs (holdings/activity/valuation) need their incremental-fetch watermark;
    # _live_or_empty forwards any extra kwargs (here ``since``) to the wrapped fetch unchanged.
    seen: dict[str, object] = {}

    def fetch(code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        seen.update(code=code, fetched_at=fetched_at, since=since)
        return []

    ingest._live_or_empty(
        fetch, "561010", source="trading_activity", fetched_at="t", since="2026-06-05"
    )
    assert seen == {"code": "561010", "fetched_at": "t", "since": "2026-06-05"}


def test_ingest_deadline_none_is_unbounded() -> None:
    # No budget set → the run is never cut short. This is the offline/test default, so the suite and
    # the golden artifact are unaffected by the wall-clock backstop.
    assert ingest.IngestDeadline(None).exceeded() is False


def test_ingest_deadline_trips_once_the_budget_elapses() -> None:
    ticks = iter([100.0, 100.5, 102.0])  # start, a check within 1s, a check past it
    deadline = ingest.IngestDeadline(1.0, clock=lambda: next(ticks))
    assert deadline.exceeded() is False  # 0.5s elapsed < 1s budget
    assert deadline.exceeded() is True  # 2.0s elapsed ≥ 1s budget


def test_ingest_deadline_zero_or_negative_is_unbounded() -> None:
    # 0/negative means "no cap" (mirroring live_pacing_seconds's "set to 0 to disable"), not "stop
    # immediately" — so `--deadline 0` can't silently truncate the whole gather on the first check.
    assert ingest.IngestDeadline(0).exceeded() is False
    assert ingest.IngestDeadline(-5.0).exceeded() is False


def test_bounded_propagates_on_persistent_failure(monkeypatch) -> None:
    # Unlike _live_or_empty, _bounded does NOT swallow to [] — a no-safe-empty read (the universe
    # membership snapshot) must fail loudly, not ship an empty universe.
    monkeypatch.setattr("random.uniform", lambda _lo, _hi: 0.0)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def always_down() -> list[Reading]:
        raise RuntimeError("universe host down")

    with pytest.raises(RuntimeError, match="universe host down"):
        ingest._bounded(always_down)


def test_bounded_returns_the_result_on_success() -> None:
    assert ingest._bounded(lambda: ["ok"]) == ["ok"]  # the read's value passes through unchanged


def test_universe_snapshot_deadline_is_wider_than_the_per_fund_one() -> None:
    # The whole-universe pull takes ~30s; the 20s per-fund deadline fails a working pull (it did, in
    # a cold-start run), so the no-safe-empty reads get a wider deadline.
    assert ingest._UNIVERSE_TIMEOUT_SECONDS > ingest._TIMEOUT_SECONDS


def test_prices_gather_stops_early_when_the_deadline_trips(monkeypatch, caplog) -> None:
    # One wedged leg must not stall a nightly run: with a wall-clock budget the per-code price loop
    # stops once the deadline trips, shipping a partial-but-valid set rather than running unbounded.
    from factor_scope.markets import ashare

    class _Feed:
        def price_sources(self, code, *, fetched_at, since=None):
            bar = Reading(
                series=prices.SERIES,
                key=code,
                as_of="2026-06-05",
                fetched_at=fetched_at,
                payload={"nav": 1.0, "source": prices.SOURCE},
            )
            return [[bar], [], []]

    ticks = iter([0.0, 0.4, 2.0])  # start, first code within budget, second code past it
    deadline = ingest.IngestDeadline(1.0, clock=lambda: next(ticks))

    with caplog.at_level(logging.WARNING):
        out = ashare.ASharePrices().gather(
            Config(source="live"),
            ["000001", "000002"],
            as_of="2026-06-05",
            fetched_at="t",
            feed=_Feed(),
            required=[],
            deadline=deadline,
        )
    assert {r.key for r in out} == {"000001"}  # only the first; the second was past the deadline
    assert any("budget" in r.message.lower() for r in caplog.records)


def test_prices_gather_deadline_does_not_falsely_pass_the_breaker(monkeypatch, caplog) -> None:
    # The blocker the review caught: when a deadline truncates the price loop, required (book) codes
    # never reached must NOT count toward the breaker's health (which lets a cold-start run ship a
    # wholly-unpriced book while logging "N/N corroborated"). The breaker reasons only over codes
    # actually attempted; the unreached book is surfaced loudly instead.
    from factor_scope.markets import ashare

    class _Feed:
        def price_sources(self, code, *, fetched_at, since=None):
            bar = Reading(
                series=prices.SERIES,
                key=code,
                as_of="2026-06-05",
                fetched_at=fetched_at,
                payload={"nav": 1.0, "source": prices.SOURCE},
            )
            return [[bar], [], []]

    ticks = iter([0.0, 100.0])  # start, then the first per-code check is already past the 1s budget
    deadline = ingest.IngestDeadline(1.0, clock=lambda: next(ticks))
    book = ["000001", "000002", "000003"]  # the whole book is required, nothing yet stored
    with caplog.at_level(logging.INFO):
        out = ashare.ASharePrices().gather(
            Config(source="live"),
            book,
            as_of="2026-06-05",
            fetched_at="t",
            feed=_Feed(),
            required=book,
            deadline=deadline,
        )
    assert out == []  # the deadline tripped before any code was priced
    msgs = " ".join(r.message.lower() for r in caplog.records)
    # The unreached book is surfaced loudly with its count — not silently dropped.
    assert "budget exceeded" in msgs and "3/3 required" in msgs and "unpriced" in msgs
    # And the breaker does NOT report the full book as corroborated (the false-health bug).
    assert "3/3 funds corroborated" not in msgs


def test_gather_live_degrades_a_failing_per_fund_leg(monkeypatch, caplog) -> None:
    # A per-fund factor leg that raises (a transient outage) must degrade to no reading for that
    # fund — its factor falls to invalid — not abort the whole universe loop. The failure is logged.
    _stub_adapters(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda _lo, _hi: 0.0)  # no real backoff between retries

    def blocked(code: str, *, fetched_at: str, since: str | None = None) -> list[Reading]:
        raise ConnectionError("valuation host blocked")

    monkeypatch.setattr(fundamentals, "fetch_live", blocked)
    with caplog.at_level(logging.WARNING):
        readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    assert {r.key for r in readings if r.series == "fund_holdings"}  # the loop was not aborted
    assert not [r for r in readings if r.series == "fundamentals"]  # the failing leg → no reading
    assert any("fundamentals" in rec.getMessage() for rec in caplog.records)


def test_gather_live_degrades_a_failing_macro_series(monkeypatch, caplog) -> None:
    # The book-wide macro dial runs behind the same resilience boundary as the per-fund legs: one
    # FRED series raising (a transient outage) degrades just that series to no reading, not the run.
    _stub_adapters(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda _lo, _hi: 0.0)  # no real backoff between retries

    def flaky(series_id: str, *, fetched_at: str) -> list[Reading]:
        if series_id == "WALCL":
            raise ConnectionError("FRED endpoint down")
        return [Reading(series="fred", key=series_id, as_of="2026-06-05", fetched_at=fetched_at,
                        payload={"series_id": series_id, "value": 1.0})]

    monkeypatch.setattr(fred, "fetch_live", flaky)
    with caplog.at_level(logging.WARNING):
        readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    assert {r.key for r in readings if r.series == "fund_holdings"}  # the run was not aborted
    macro = {r.key for r in readings if r.series == "fred"}
    assert "WALCL" not in macro  # the failing series degraded to no reading
    assert "DGS10" in macro  # the surviving series still landed
    assert any("WALCL" in rec.getMessage() for rec in caplog.records)


def test_gather_live_degrades_a_missing_fred_api_key(monkeypatch, caplog) -> None:
    # The exact Issue #2 scenario: no FRED_API_KEY → fredapi raises ValueError. The macro dial must
    # degrade to no reading (factor invalid), not abort the run after the expensive universe pull.
    _stub_adapters(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda _lo, _hi: 0.0)

    def no_key(series_id: str, *, fetched_at: str) -> list[Reading]:
        raise ValueError("You need to set a valid API key.")  # mirrors fredapi with no key

    monkeypatch.setattr(fred, "fetch_live", no_key)
    with caplog.at_level(logging.WARNING):
        readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    assert {r.key for r in readings if r.series == "fund_holdings"}  # the run completed
    assert not [r for r in readings if r.series == "fred"]  # the macro dial degraded
    assert any("fred" in rec.getMessage().lower() for rec in caplog.records)


def test_gather_live_degrades_a_failing_demand_leg(monkeypatch, caplog) -> None:
    # The keyless, book-wide end-demand dial must degrade like the others when AkShare blocks it —
    # exercising the keyless-adapter shim end-to-end — rather than aborting the run.
    _stub_adapters(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda _lo, _hi: 0.0)

    def blocked(*, fetched_at: str) -> list[Reading]:
        raise RuntimeError("AkShare blocked")

    monkeypatch.setattr(demand, "fetch_live", blocked)
    with caplog.at_level(logging.WARNING):
        readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    assert {r.key for r in readings if r.series == "fund_universe"}  # the run completed
    assert not [r for r in readings if r.series == "demand"]  # the demand dial degraded
    assert any("demand" in rec.getMessage() for rec in caplog.records)


def test_gather_live_degrades_a_failing_edgar_filer(monkeypatch, caplog) -> None:
    # One EDGAR filer raising must degrade just that CIK — surviving filers' holdings still land,
    # not lost because one CIK in the loop aborted the whole gather.
    _stub_adapters(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda _lo, _hi: 0.0)

    def flaky(cik: str, *, form: str = "13F-HR", fetched_at: str) -> list[Reading]:
        if cik == "0001067983":
            raise TimeoutError("EDGAR slow")
        return [Reading(series="edgar", key=f"{cik}/COHR", as_of="2026-03-31",
                        fetched_at=fetched_at,
                        payload={"filer": cik, "holding": "COHR", "weight": 0.05, "form": form})]

    monkeypatch.setattr(edgar, "fetch_live", flaky)
    config = Config(source="live", edgar_ciks=("0001067983", "0000102909"))
    with caplog.at_level(logging.WARNING):
        readings = AShareMarket().gather(config, as_of="2026-06-05")

    filers = {r.payload["filer"] for r in readings if r.series == "edgar"}
    assert filers == {"0000102909"}  # the failing CIK degraded; the surviving one still landed
    assert any("0001067983" in rec.getMessage() for rec in caplog.records)


def test_ingest_preflight_fails_before_gather(monkeypatch, tmp_path) -> None:
    # The preflight runs inside pipeline.ingest before gather, so a missing key fails in seconds —
    # never after the multi-hour universe pull. Inject a market whose gather must not run.
    from factor_scope.markets.ashare import CredentialError
    from factor_scope.pipeline import ingest as nightly_ingest

    monkeypatch.delenv("FRED_API_KEY", raising=False)

    class _ExplodingMarket:
        name = "ashare"

        def gather(self, config, *, as_of, store=None):
            raise AssertionError("gather must not run when the preflight fails")

    paths = {"store_path": tmp_path / "s.duckdb", "graph_path": tmp_path / "g.ladybug"}
    with pytest.raises(CredentialError, match="FRED_API_KEY"):
        nightly_ingest(
            Config(source="live", as_of="2026-06-05", **paths), market=_ExplodingMarket()
        )


def test_ingest_does_not_preflight_credentials_when_offline(monkeypatch, tmp_path) -> None:
    # The preflight is gated on source=="live"; the forced-offline suite (fixtures) must never trip
    # it even with no FRED_API_KEY set — otherwise the whole offline suite would break.
    from factor_scope.pipeline import ingest as nightly_ingest

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    paths = {"store_path": tmp_path / "s.duckdb", "graph_path": tmp_path / "g.ladybug"}
    n = nightly_ingest(Config(source="fixtures", as_of="2026-06-05", **paths))
    assert n > 0  # the offline ingest ran normally, no CredentialError raised
