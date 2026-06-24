"""Incremental watermark ingest — a re-pull fetches only what is newer than the store holds.

The per-fund time series (trading activity, valuation, holdings) are re-pulled every night over the
whole on-exchange universe. Before each pull the universe loop reads that ``(series, key)``'s latest
stored ``as_of`` and asks the adapter for only newer observations, so a second consecutive ingest
re-fetches/writes nothing already held and a skipped night is backfilled from the watermark — not a
hard-coded lookback. These stay offline by stubbing the heavy ``fetch_live`` backends with
date-driven fakes that record the ``since`` watermark each call was handed.
"""

import pytest

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
from factor_scope.markets import ashare
from factor_scope.pipeline import ingest as nightly_ingest
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.integration

# One on-exchange ETF (561010 is in the valuation tracked-index map) is enough to pin the watermark.
_FUND = "561010"

# What each source *could* serve up to a given run date — the universe loop must carve only the
# newer-than-watermark slice out of these.
_TRADING_BARS = ("2026-06-29", "2026-06-30", "2026-09-29", "2026-09-30")
_VALUATION_BARS = ("2026-06-29", "2026-06-30", "2026-09-29", "2026-09-30")
_HOLDINGS_QUARTERS = ("2026-03-31", "2026-06-30", "2026-09-30")
_PRICE_BARS = ("2026-06-29", "2026-06-30", "2026-09-29", "2026-09-30")


def _stub_adapters(monkeypatch) -> dict[str, list[str | None]]:
    """Install date-driven fakes for each live backend; return the recorded ``since`` per series."""

    # A configured live host with the network stubbed: the credential preflight needs the key set.
    monkeypatch.setenv("FRED_API_KEY", "stub-key")
    seen: dict[str, list[str | None]] = {
        trading_activity.SERIES: [],
        fundamentals.SERIES: [],
        fund_holdings.SERIES: [],
        prices.SERIES: [],
    }

    def _newer(dates, since, run):
        return [d for d in dates if d <= run and (since is None or d > since)]

    def fake_trading(code, *, fetched_at, since=None):
        seen[trading_activity.SERIES].append(since)
        return [
            Reading(series=trading_activity.SERIES, key=code, as_of=d, fetched_at=fetched_at,
                    payload={"turnover": 3.1, "amount": 2.8})
            for d in _newer(_TRADING_BARS, since, fetched_at[:10])
        ]

    def fake_valuation(code, *, fetched_at, since=None):
        seen[fundamentals.SERIES].append(since)
        return [
            Reading(series=fundamentals.SERIES, key=code, as_of=d, fetched_at=fetched_at,
                    payload={"pe": 42.5})
            for d in _newer(_VALUATION_BARS, since, fetched_at[:10])
        ]

    def fake_holdings(fund, *, fetched_at, since=None):
        seen[fund_holdings.SERIES].append(since)
        return [
            Reading(series=fund_holdings.SERIES, key=f"{fund}/X", as_of=q, fetched_at=fetched_at,
                    payload={"fund": fund, "holding": "X", "weight": 0.1})
            for q in _newer(_HOLDINGS_QUARTERS, since, fetched_at[:10])
        ]

    monkeypatch.setattr(trading_activity, "fetch_live", fake_trading)
    monkeypatch.setattr(fundamentals, "fetch_live", fake_valuation)
    monkeypatch.setattr(fund_holdings, "fetch_live", fake_holdings)
    monkeypatch.setattr(
        fund_universe,
        "fetch_live",
        lambda *, as_of, fetched_at: [
            Reading(series="fund_universe", key=_FUND, as_of=as_of, fetched_at=fetched_at,
                    payload={"name": _FUND, "type": "ETF", "on_exchange": True,
                             "inception": "2021-01-20", "delisting": "", "fee": None,
                             "tracking_error": None, "top10_weight": None, "valid": False})
        ],
    )
    # Prices are watermarked windows too: the AkShare leg is date-driven (and records its ``since``)
    # so a re-pull carves only the newer-than-watermark slice; Baostock/Mootdx corroborate the
    # latest bar so the run completes without tripping the data circuit breaker. Only the AkShare
    # leg records ``since`` — the watermark progression is asserted on it.
    def fake_price(key, *, fetched_at, since=None, impersonate="chrome"):
        if key == _FUND:  # the universe ETF whose price watermark the test pins
            seen[prices.SERIES].append(since)
        return [
            Reading(series="prices", key=key, as_of=d, fetched_at=fetched_at, payload={"nav": 1.0})
            for d in _newer(_PRICE_BARS, since, fetched_at[:10])
        ]

    monkeypatch.setattr(prices, "fetch_live", fake_price)
    for source in (baostock, mootdx):
        monkeypatch.setattr(
            source,
            "fetch_live",
            lambda key, *, fetched_at, since=None: [
                Reading(series="prices", key=key, as_of=fetched_at[:10], fetched_at=fetched_at,
                        payload={"nav": 1.0})
            ],
        )
    monkeypatch.setattr(
        etf_scale,
        "fetch_live",
        lambda *, fetched_at: [
            Reading(series="etf_scale", key=_FUND, as_of=fetched_at[:10], fetched_at=fetched_at,
                    payload={"exchange": "sse", "aum": 68.0, "shares": 40.0})
        ],
    )
    monkeypatch.setattr(fred, "fetch_live", lambda series_id, *, fetched_at: [])
    monkeypatch.setattr(demand, "fetch_live", lambda *, fetched_at: [])
    monkeypatch.setattr(edgar, "fetch_live", lambda cik, *, form="13F-HR", fetched_at: [])
    return seen


def _counts(store_path) -> dict[str, int]:
    store = DuckDBStore(store_path)
    try:
        return {
            series: store.count(series)
            for series in (trading_activity.SERIES, fundamentals.SERIES, fund_holdings.SERIES)
        }
    finally:
        store.close()


def test_second_ingest_pulls_only_newer_bars(monkeypatch, tmp_path) -> None:
    seen = _stub_adapters(monkeypatch)
    # A live pull stamps the real wall clock; pin it to each night's run date so the date-driven
    # stubs (which read the source's latest from ``fetched_at[:10]``) model a source grown to the
    # run date — the production case where the host clock tracks the trading day it ingests.
    monkeypatch.setattr(
        ashare, "fetched_at_now",
        iter(["2026-06-30T22:00:00Z", "2026-09-30T22:00:00Z"]).__next__,
    )
    paths = {"store_path": tmp_path / "store.duckdb", "graph_path": tmp_path / "graph.duckdb"}

    # Night one: the store is empty, so every series is pulled from scratch (no watermark).
    nightly_ingest(Config(source="live", as_of="2026-06-30", **paths))
    assert _counts(paths["store_path"]) == {
        trading_activity.SERIES: 2,  # 06-29, 06-30
        fundamentals.SERIES: 2,  # 06-29, 06-30
        fund_holdings.SERIES: 2,  # 2026-03-31, 2026-06-30
    }
    assert seen[trading_activity.SERIES] == [None]
    assert seen[fundamentals.SERIES] == [None]
    assert seen[fund_holdings.SERIES] == [None]
    assert seen[prices.SERIES] == [None]  # cold start → the price leg seeds its window from scratch

    # Night two skips ahead a quarter: each pull is handed the night-one watermark and returns only
    # the strictly-newer slice — the skipped sessions in between are backfilled from the watermark.
    nightly_ingest(Config(source="live", as_of="2026-09-30", **paths))
    assert seen[trading_activity.SERIES] == [None, "2026-06-30"]
    assert seen[fundamentals.SERIES] == [None, "2026-06-30"]
    assert seen[fund_holdings.SERIES] == [None, "2026-06-30"]
    assert seen[prices.SERIES] == [None, "2026-06-30"]  # night two pulls only past the price floor
    assert _counts(paths["store_path"]) == {
        trading_activity.SERIES: 4,  # + 09-29, 09-30 (the gap is backfilled, not just last night)
        fundamentals.SERIES: 4,  # + 09-29, 09-30
        fund_holdings.SERIES: 3,  # + the new 2026-09-30 quarter only
    }

    store = DuckDBStore(paths["store_path"])
    try:
        bars = [r.as_of for r in store.history(trading_activity.SERIES, _FUND)]
        price_bars = [r.as_of for r in store.history(prices.SERIES, _FUND)]
    finally:
        store.close()
    assert bars == list(_TRADING_BARS)  # the full series, each bar stored exactly once
    assert price_bars == list(_PRICE_BARS)  # windowed seed + incremental backfill, one bar per date


def test_reingest_same_night_writes_nothing(monkeypatch, tmp_path) -> None:
    seen = _stub_adapters(monkeypatch)
    # Pin the live pull's wall clock to the run date so the date-driven stubs see the source's
    # latest as that night (see the note in test_second_ingest_pulls_only_newer_bars).
    monkeypatch.setattr(ashare, "fetched_at_now", lambda: "2026-09-30T22:00:00Z")
    paths = {"store_path": tmp_path / "store.duckdb", "graph_path": tmp_path / "graph.duckdb"}

    nightly_ingest(Config(source="live", as_of="2026-09-30", **paths))
    before = _counts(paths["store_path"])
    # Re-running the identical night reads the just-written watermark and asks for nothing newer —
    # the re-pull is a no-op, so the append-only log does not grow.
    nightly_ingest(Config(source="live", as_of="2026-09-30", **paths))
    assert _counts(paths["store_path"]) == before
    assert seen[trading_activity.SERIES][-1] == "2026-09-30"  # the watermark, not a re-pull of all


def test_offline_reingest_over_unchanged_cassettes_writes_nothing(tmp_path) -> None:
    # The cassette ingest runs the *same* universe loop + watermark + content dedup as live (no
    # stubs here — the real offline feed replays the committed recordings). A second ingest over the
    # unchanged snapshot asks each watermarked series for nothing newer and the dedup drops the
    # full-snapshot re-pulls (universe / scale / prices), so the append-only log does not grow.
    paths = {"store_path": tmp_path / "store.duckdb", "graph_path": tmp_path / "graph.duckdb"}
    first = nightly_ingest(Config(**paths))  # the suite forces offline (FACTOR_SCOPE_OFFLINE=1)
    second = nightly_ingest(Config(**paths))
    assert first > 0  # the first ingest fills the store from the recordings
    assert second == 0  # the re-pull is a pure no-op — watermark + dedup, exercised offline

    store = DuckDBStore(paths["store_path"])
    try:
        bars = [r.as_of for r in store.history(trading_activity.SERIES, _FUND)]
    finally:
        store.close()
    assert bars == sorted(bars) and len(bars) == len(set(bars))  # each session stored exactly once


def test_a_provisional_spot_bar_does_not_set_the_history_floor(tmp_path) -> None:
    # A spot-board bar is the current session only — were it to set the incremental floor, the next
    # history pull would start past it and never backfill the sessions the outage skipped. Tagged
    # provisional, it is excluded from the floor, so a recovered history pull self-heals the gap.
    store = DuckDBStore(tmp_path / "store.duckdb")
    try:
        store.append(
            [
                Reading(series=trading_activity.SERIES, key=_FUND, as_of="2026-06-10",
                        fetched_at="2026-06-10T22:00:00Z",
                        payload={"turnover": 1.0, "amount": 1.0}),
                Reading(series=trading_activity.SERIES, key=_FUND, as_of="2026-06-12",
                        fetched_at="2026-06-12T22:00:00Z",
                        payload={"turnover": 2.0, "amount": 2.0, "provisional": True}),
            ]
        )
        floors = ashare._series_watermarks(store, trading_activity.SERIES, "2026-06-12")
    finally:
        store.close()
    assert _FUND not in floors  # no floor → the recovered history pull backfills from scratch
