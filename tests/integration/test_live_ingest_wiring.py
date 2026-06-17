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
from factor_scope.ingest.base import IngestError
from factor_scope.markets.ashare import AShareMarket
from factor_scope.store import Reading

pytestmark = pytest.mark.integration

# A small live universe stand-in: two on-exchange ETFs (whose holdings the graph needs) + one
# off-exchange fund (no holdings refresh). The held positions fixture is priced independently.
_UNIVERSE = (("561010", True), ("588200", True), ("000001", False))


def _stub_adapters(monkeypatch) -> None:
    monkeypatch.setattr(
        prices,
        "fetch_live",
        lambda key, *, fetched_at: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.0})
        ],
    )
    monkeypatch.setattr(
        baostock,
        "fetch_live",
        lambda key, *, fetched_at: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.0})  # corroborates the AkShare read
        ],
    )
    monkeypatch.setattr(
        mootdx,
        "fetch_live",
        lambda key, *, fetched_at: [
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
    monkeypatch.setattr(
        fund_universe,
        "fetch_live",
        lambda *, as_of, fetched_at: [
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
        lambda *, fetched_at: [
            Reading(series="etf_scale", key="561010", as_of="2026-05-31", fetched_at=fetched_at,
                    payload={"exchange": "sse", "aum": 68.0, "shares": 40.0})
        ],
    )
    monkeypatch.setattr(
        trading_activity,
        "fetch_live",
        lambda code, *, fetched_at, since=None: [
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
        lambda *, as_of, fetched_at: [
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

    def _akshare_down(key, *, fetched_at):
        raise RuntimeError("AkShare IP-blocked")

    monkeypatch.setattr(prices, "fetch_live", _akshare_down)
    for source in (baostock, mootdx):  # the two surviving sources agree on the substitute NAV
        monkeypatch.setattr(
            source,
            "fetch_live",
            lambda key, *, fetched_at: [
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
        lambda key, *, fetched_at: [
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
        lambda key, *, fetched_at: [
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
        lambda key, *, fetched_at: [
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
