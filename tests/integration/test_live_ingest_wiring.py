"""The ``--live`` ingest path refreshes holdings, not just prices + the macro dial.

These stay offline by stubbing each adapter's heavy ``fetch_live`` (the real bodies hit the
network and live behind ``FACTOR_SCOPE_LIVE=1`` in ``test_adapters_live.py``). They pin the wiring
of the A-share market's live gather: every held fund's holdings are refreshed (so the connection
graph rebuilds from live disclosures) and each configured EDGAR filer is pulled.
"""

import logging
import time

import pytest

from factor_scope import ingest
from factor_scope.config import Config
from factor_scope.ingest import (
    baostock,
    edgar,
    fred,
    fund_holdings,
    mootdx,
    prices,
)
from factor_scope.ingest.base import IngestError
from factor_scope.markets.ashare import AShareMarket
from factor_scope.store import Reading

pytestmark = pytest.mark.integration


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
        lambda fund, *, fetched_at: [
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


def test_gather_live_refreshes_fund_holdings_per_held_fund(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    readings = AShareMarket().gather(Config(source="live"), as_of="2026-06-05")

    held = [r for r in readings if r.series == "positions"]
    refreshed = [r for r in readings if r.series == "fund_holdings"]
    assert held  # the offline positions fixture is the universe
    # one live holdings refresh per held fund — so the graph rebuilds from live disclosures
    assert {r.payload["fund"] for r in refreshed} == {r.key for r in held}


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
