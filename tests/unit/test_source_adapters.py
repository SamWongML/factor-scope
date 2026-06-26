"""Unit tests for the live network backends (``fetch_live``) of the CN price + holdings adapters.

The fixture-parser path is gone: offline replays cassettes through the feed (see
``tests/unit/test_feed.py``); these pin the live AkShare/Baostock/Mootdx/EdgarTools mappings and the
multi-source NAV reconciliation, plus the seed adapters (FRED / EDGAR / themes) the offline run
still loads from CSV.
"""

import sys
import types

import pytest

from factor_scope.ingest import (
    baostock,
    edgar,
    fred,
    fund_holdings,
    mootdx,
    prices,
    themes,
)
from factor_scope.ingest.base import IngestError
from factor_scope.store import Reading
from tests.unit._akshare_fakes import FakeFrame, install_fake_akshare

pytestmark = pytest.mark.unit


def _em_bar(as_of: str, close: float) -> dict:
    """A domain bar as the EastMoney client returns it; the NAV leg reads only date + close."""

    return {"date": as_of, "close": close, "turnover": 0.0, "amount": 0.0}


def test_sina_symbol_prefixes_by_listing_exchange() -> None:
    assert prices._sina_symbol("561010") == "sh561010"  # SSE ETFs are 5x
    assert prices._sina_symbol("159915") == "sz159915"  # SZSE ETFs are 1x


def test_from_kline_maps_close_to_nav() -> None:
    reading = prices.from_kline("561010", [_em_bar("2026-06-16", 0.918)], fetched_at="t")[0]
    assert reading.as_of == "2026-06-16"
    assert reading.payload == {"nav": 0.918, "source": "akshare"}


def test_from_kline_returns_the_whole_window_not_just_the_latest() -> None:
    # The trend/reversal/low-vol factors read the full stored NAV history; the price leg must store
    # every bar it pulls, not just the latest, or the 200-day MA gate is blind on a cold start.
    bars = [
        _em_bar("2026-06-14", 0.910),
        _em_bar("2026-06-15", 0.915),
        _em_bar("2026-06-16", 0.918),
    ]
    readings = prices.from_kline("561010", bars, fetched_at="2026-06-16T22:00:00Z")
    assert [r.as_of for r in readings] == ["2026-06-14", "2026-06-15", "2026-06-16"]
    assert readings[-1].payload == {"nav": 0.918, "source": "akshare"}


def test_from_kline_keeps_only_bars_past_the_floor() -> None:
    # the incremental re-pull drops bars at or before the stored watermark — only newer rows.
    bars = [_em_bar("2026-06-15", 0.915), _em_bar("2026-06-16", 0.918)]
    readings = prices.from_kline(
        "561010", bars, fetched_at="2026-06-16T22:00:00Z", floor="2026-06-15"
    )
    assert [r.as_of for r in readings] == ["2026-06-16"]


def _spot_row(price: float = 0.918) -> dict:
    """One normalised domain spot-board row carrying the current bar — ``nav`` is the NAV basis."""

    return {"date": "2026-06-16", "nav": price}


def test_spot_reading_maps_latest_price_to_nav_settled() -> None:
    rows = prices.spot_reading(
        {"561010": _spot_row()}, "561010", fetched_at="t", settled=True, floor=None
    )
    assert rows[0].as_of == "2026-06-16"
    assert rows[0].payload == {"nav": 0.918, "source": "akshare"}  # settled → no provisional tag


def test_spot_reading_is_provisional_when_not_settled() -> None:
    # An unsettled (intraday/holiday/outage) bar is the current session only; the marker lets the
    # incremental floor skip it so a later K-line pull backfills the real settled close.
    rows = prices.spot_reading(
        {"561010": _spot_row()}, "561010", fetched_at="t", settled=False, floor=None
    )
    assert rows[0].payload == {"nav": 0.918, "source": "akshare", "provisional": True}


def test_spot_reading_yields_no_reading_for_a_fund_absent_from_the_board() -> None:
    assert (
        prices.spot_reading(
            {"515880": _spot_row()}, "561010", fetched_at="t", settled=True, floor=None
        )
        == []
    )


def test_sina_trims_full_history_to_the_window_floor(monkeypatch) -> None:
    requested: list[str] = []

    def sina(symbol: str) -> FakeFrame:
        # Sina has no date range — it serves the entire history, so the window is trimmed by floor.
        requested.append(symbol)
        return FakeFrame(
            [{"date": "2019-01-02", "close": 0.5}, {"date": "2026-06-16", "close": 0.918}]
        )

    install_fake_akshare(monkeypatch, fund_etf_hist_sina=sina)
    readings = prices.sina("561010", fetched_at="2026-06-16T22:00:00Z", floor="2024-09-05")
    assert requested == ["sh561010"]  # routed to Sina with the exchange prefix
    assert [r.as_of for r in readings] == ["2026-06-16"]  # the ancient bar is outside the window
    assert readings[0].payload == {"nav": 0.918, "source": "akshare"}  # still the akshare leg


def test_em_start_cold_seed_bounds_the_window_instead_of_full_history() -> None:
    # no watermark → ~650 calendar days back (≈ 400 trading days), not the 19700101 epoch
    beg = prices._em_start("2026-06-16T22:00:00Z", None)
    assert beg != "19700101"
    assert beg.startswith("2024")


def test_em_start_incremental_requests_one_day_past_the_watermark() -> None:
    assert prices._em_start("2026-06-16T22:00:00Z", "2026-06-15") == "20260616"  # watermark + 1 day


def test_fund_holdings_first_year_is_the_watermark_year_else_the_run_year() -> None:
    # No watermark → start at the run stamp's year (no hard-coded lookback, no wall clock).
    assert fund_holdings._first_year(None, "2026-09-30T22:00:00Z") == 2026
    # A watermark from a prior year → re-request that year forward so a year-boundary gap backfills.
    assert fund_holdings._first_year("2025-12-31", "2026-09-30T22:00:00Z") == 2025
    assert fund_holdings._first_year("2026-06-30", "2026-09-30T22:00:00Z") == 2026


def test_fred_keys_by_series_id() -> None:
    readings = fred.parse("series_id,as_of,value\nDGS10,2026-06-04,4.21\n", fetched_at="x")
    assert readings[0].key == "DGS10"
    assert readings[0].payload == {"series_id": "DGS10", "value": 4.21}


def test_edgar_keys_each_position() -> None:
    text = "filer,as_of,holding,shares\n0001067983,2026-03-31,COHR,1250000\n"
    readings = edgar.parse(text, fetched_at="x")
    assert readings[0].key == "0001067983/COHR"
    assert readings[0].payload["shares"] == 1250000.0


class _FakeTable:
    """A minimal pandas-free stand-in for an EdgarTools holdings table."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def iterrows(self):
        return enumerate(self._rows)


class _Fake13FObj:
    def __init__(self, rows: list[dict]) -> None:
        self.infotable = _FakeTable(rows)


class _FakeNportObj:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def investment_data(self) -> _FakeTable:
        return _FakeTable(self._rows)


class _FakeFiling:
    def __init__(self, obj: object) -> None:
        self._obj = obj
        self.filing_date = "2026-03-31"

    def obj(self) -> object:
        return self._obj


class _FakeFilings:
    def __init__(self, filing: _FakeFiling) -> None:
        self._filing = filing

    def latest(self, n: int) -> _FakeFiling:
        return self._filing


def _install_fake_edgar(
    monkeypatch, *, requested: list[str], identities: list[str] | None = None
) -> None:
    """A network-free ``edgar`` module recording the requested form (and any set identity)."""

    def get_filings(form: str) -> _FakeFilings:
        requested.append(form)
        if form == "13F-HR":
            obj: object = _Fake13FObj([{"Ticker": "COHR", "SharesPrnAmount": 1_250_000}])
        else:  # NPORT-P
            obj = _FakeNportObj([{"name": "APPLE INC", "pct_value": 7.5}])
        return _FakeFilings(_FakeFiling(obj))

    class _FakeCompany:
        def __init__(self, cik: str) -> None:
            self.cik = cik

        def get_filings(self, form: str) -> _FakeFilings:
            return get_filings(form)

    def set_identity(identity: str) -> None:
        if identities is not None:
            identities.append(identity)

    module = types.ModuleType("edgar")
    module.Company = _FakeCompany  # type: ignore[attr-defined]
    module.set_identity = set_identity  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edgar", module)


def test_edgar_fetch_live_defaults_to_13f(monkeypatch) -> None:
    requested: list[str] = []
    _install_fake_edgar(monkeypatch, requested=requested)
    readings = edgar.fetch_live("0001067983", fetched_at="t")
    assert requested == ["13F-HR"]
    assert readings[0].key == "0001067983/COHR"
    assert readings[0].payload == {"filer": "0001067983", "holding": "COHR", "shares": 1_250_000.0}


def test_edgar_fetch_live_supports_nport(monkeypatch) -> None:
    requested: list[str] = []
    _install_fake_edgar(monkeypatch, requested=requested)
    readings = edgar.fetch_live("0000102909", form="NPORT-P", fetched_at="t")
    assert requested == ["NPORT-P"]
    assert readings[0].key == "0000102909/APPLE INC"
    # pct_value (% of net assets) → fraction weight, so N-PORT holdings can be graph HOLDS edges
    assert readings[0].payload == {"filer": "0000102909", "holding": "APPLE INC", "weight": 0.075}


def test_edgar_fetch_live_sets_the_resolved_identity(monkeypatch) -> None:
    # The SEC refuses requests without a User-Agent identity; the adapter resolves EDGAR_IDENTITY
    # (env-then-Keychain) and sets it on EdgarTools itself, so the launchd nightly is self-reliant.
    monkeypatch.setenv("EDGAR_IDENTITY", "Jane Doe jane@x.com")
    identities: list[str] = []
    _install_fake_edgar(monkeypatch, requested=[], identities=identities)
    edgar.fetch_live("0001067983", fetched_at="t")
    # set before the pull, from the resolved credential
    assert identities == ["Jane Doe jane@x.com"]


def test_themes_keys_by_name_and_coerces_flags() -> None:
    text = (
        "theme,as_of,acceleration,base_level,breadth,crowding,"
        "broad_adoption,path_to_profit,fad_resistant,lead_chain,wrapper_exists,constituents\n"
        "储能,2026-05-31,0.62,0.30,6,0.35,1,1,1,1,0,宁德时代;阳光电源\n"
    )
    readings = themes.parse(text, fetched_at="x")
    assert readings[0].series == "themes"
    assert readings[0].key == "储能"
    assert readings[0].as_of == "2026-05-31"  # the research date, not the run date
    assert readings[0].payload["breadth"] == 6  # parsed as an int
    assert readings[0].payload["broad_adoption"] is True
    assert readings[0].payload["wrapper_exists"] is False  # 0 → False
    assert readings[0].payload["constituents"] == ["宁德时代", "阳光电源"]  # ;-split names


class _FakeResultSet:
    """A pandas-free stand-in for a baostock ``query_history_k_data_plus`` result."""

    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = rows
        self._i = -1

    def next(self) -> bool:
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._i]


def _install_fake_baostock(monkeypatch, *, rows: list[list[str]], calls: list[dict]) -> None:
    """Inject a network-free ``baostock`` module that records each query (code + kwargs)."""

    module = types.ModuleType("baostock")
    module.login = lambda: None  # type: ignore[attr-defined]
    module.logout = lambda: None  # type: ignore[attr-defined]

    def query(code: str, fields: str, **kwargs: object) -> _FakeResultSet:
        calls.append({"code": code, **kwargs})
        return _FakeResultSet(rows)

    module.query_history_k_data_plus = query  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "baostock", module)


def test_baostock_fetch_live_returns_the_window(monkeypatch) -> None:
    calls: list[dict] = []
    _install_fake_baostock(
        monkeypatch,
        rows=[["2026-06-04", "1.90"], ["2026-06-05", "1.92"]],
        calls=calls,
    )
    readings = baostock.fetch_live("561010", fetched_at="2026-06-05T22:00:00Z")
    assert calls[0]["code"] == "sh.561010"  # 5x ETF codes are Shanghai-listed
    assert [r.as_of for r in readings] == ["2026-06-04", "2026-06-05"]  # the whole window, not [-1]
    assert all(r.series == "prices" and r.key == "561010" for r in readings)
    # provenance: every price reading records which source it came from
    assert readings[-1].payload == {"nav": 1.92, "source": "baostock"}


def test_baostock_fetch_live_keeps_only_bars_after_the_watermark(monkeypatch) -> None:
    calls: list[dict] = []
    _install_fake_baostock(
        monkeypatch,
        rows=[["2026-06-04", "1.90"], ["2026-06-05", "1.92"]],
        calls=calls,
    )
    readings = baostock.fetch_live("561010", fetched_at="2026-06-05T22:00:00Z", since="2026-06-04")
    assert calls[0]["start_date"] == "2026-06-05"  # the watermark + 1 day, Baostock's YYYY-MM-DD
    assert [r.as_of for r in readings] == ["2026-06-05"]  # only sessions past the watermark


def test_baostock_code_prefixes_shenzhen_for_1x(monkeypatch) -> None:
    calls: list[dict] = []
    _install_fake_baostock(monkeypatch, rows=[["2026-06-05", "0.98"]], calls=calls)
    baostock.fetch_live("159915", fetched_at="t")
    assert calls[0]["code"] == "sz.159915"  # 1x ETF codes are Shenzhen-listed


def test_baostock_requests_unadjusted_prices(monkeypatch) -> None:
    # Both legs must compare on the SAME basis: AkShare reads raw close (adjust=""), so Baostock
    # must request no-adjustment (adjustflag="3"). Otherwise split/dividend adjustments would make
    # the two sources diverge spuriously and trip the corroboration check.
    calls: list[dict] = []
    _install_fake_baostock(monkeypatch, rows=[["2026-06-05", "1.92"]], calls=calls)
    baostock.fetch_live("561010", fetched_at="t")
    assert calls[0]["adjustflag"] == "3"  # 不复权 — raw close, matching AkShare


def test_baostock_fetch_live_returns_empty_when_no_rows(monkeypatch) -> None:
    # A delisted/unknown code yields no bars → no data, not an IndexError, so the caller can
    # fall back to the other source rather than crash the run.
    _install_fake_baostock(monkeypatch, rows=[], calls=[])
    assert baostock.fetch_live("561010", fetched_at="t") == []


def test_mootdx_tags_its_source() -> None:
    assert mootdx.SOURCE == "mootdx"  # the third price source carries its own provenance tag


class _FakeBars:
    """A pandas-free stand-in for a Mootdx ``client.bars`` daily frame (index=date, col=close)."""

    def __init__(self, by_date: dict[str, float]) -> None:
        self._by_date = by_date

    @property
    def empty(self) -> bool:
        return not self._by_date

    def iterrows(self):
        for day, close in self._by_date.items():
            yield day, {"close": close}


def _install_fake_mootdx(
    monkeypatch,
    *,
    bars: _FakeBars | None,
    calls: list[dict],
    factory_calls: list[dict] | None = None,
    clients: list | None = None,
    closes: list | None = None,
    fail: bool = False,
    dead: bool = False,
) -> None:
    """Inject a network-free ``mootdx`` whose ``Quotes`` factory + client record each interaction.

    Mirrors the hardened adapter's surface: ``mootdx.consts.HQ_HOSTS`` for server pinning, a
    factory that records its ``server``/``bestip``/``timeout`` kwargs, a client exposing
    ``.client.auto_retry`` (the default the adapter flips off), ``.client.get_security_count`` (the
    liveness probe), and ``.close()``. ``bars`` is the frame returned; ``fail=True`` makes the read
    raise; ``dead=True`` makes the liveness probe return no count — the real silent-server signal
    (the live library returns an *empty frame*, not ``None``, so death can't be told from ``bars()``
    alone).
    """

    package = types.ModuleType("mootdx")
    quotes_mod = types.ModuleType("mootdx.quotes")
    consts_mod = types.ModuleType("mootdx.consts")
    consts_mod.HQ_HOSTS = [  # type: ignore[attr-defined]
        ("fake-tdx-1", "127.0.0.1", 7709),
        ("fake-tdx-2", "127.0.0.2", 7709),
    ]

    class _Api:
        def __init__(self) -> None:
            self.auto_retry = True  # mootdx's StdQuotes default — the adapter must disable it

        def get_security_count(self, market: int = 1) -> int | None:
            return None if dead else 5000  # a live TDX server returns a positive security count

    class _Client:
        def __init__(self) -> None:
            self.client = _Api()
            if clients is not None:
                clients.append(self)

        def bars(self, *, symbol: str, frequency: int, offset: int) -> _FakeBars | None:
            calls.append({"symbol": symbol, "frequency": frequency, "offset": offset})
            if fail:
                raise ConnectionError("tdx server silent")
            return bars

        def close(self) -> None:
            if closes is not None:
                closes.append(True)

    class _Quotes:
        @staticmethod
        def factory(*, market: str, server=None, bestip=None, timeout=None) -> _Client:
            if factory_calls is not None:
                factory_calls.append(
                    {"market": market, "server": server, "bestip": bestip, "timeout": timeout}
                )
            return _Client()

    quotes_mod.Quotes = _Quotes  # type: ignore[attr-defined]
    package.quotes = quotes_mod  # type: ignore[attr-defined]
    package.consts = consts_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mootdx", package)
    monkeypatch.setitem(sys.modules, "mootdx.quotes", quotes_mod)
    monkeypatch.setitem(sys.modules, "mootdx.consts", consts_mod)
    # The pinned-server cache is module-level; reset it so each test picks from the fake host list.
    monkeypatch.setattr(mootdx, "_server", None, raising=False)
    monkeypatch.setattr(mootdx, "_server_index", 0, raising=False)


def test_mootdx_fetch_live_returns_the_window(monkeypatch) -> None:
    calls: list[dict] = []
    _install_fake_mootdx(
        monkeypatch, bars=_FakeBars({"2026-06-04": 0.97, "2026-06-05": 0.98}), calls=calls
    )
    readings = mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z")
    assert [r.as_of for r in readings] == ["2026-06-04", "2026-06-05"]  # the window, not just [-1]
    assert readings[-1].payload == {"nav": 0.98, "source": "mootdx"}
    assert calls[0]["offset"] == 400  # a bounded window of recent bars, not one latest bar


def test_mootdx_keeps_only_bars_after_the_watermark(monkeypatch) -> None:
    _install_fake_mootdx(
        monkeypatch, bars=_FakeBars({"2026-06-04": 0.97, "2026-06-05": 0.98}), calls=[]
    )
    readings = mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z", since="2026-06-04")
    assert [r.as_of for r in readings] == ["2026-06-05"]  # only sessions past the watermark


def test_mootdx_returns_empty_when_no_bars(monkeypatch) -> None:
    # An unknown/delisted code yields an empty frame → no data, so the caller falls back.
    _install_fake_mootdx(monkeypatch, bars=_FakeBars({}), calls=[])
    assert mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z") == []


def test_mootdx_pins_a_server_and_bounds_the_socket(monkeypatch) -> None:
    # The leg pins a known TDX host (no per-call async server selection) and passes an explicit
    # socket timeout, so a silent server can't wedge the read — the root of the live-ingest hang.
    factory_calls: list[dict] = []
    _install_fake_mootdx(
        monkeypatch, bars=_FakeBars({"2026-06-05": 0.98}), calls=[], factory_calls=factory_calls
    )
    mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z")
    assert factory_calls[0]["server"] == ("127.0.0.1", 7709)  # pinned from HQ_HOSTS[0], not probed
    assert factory_calls[0]["bestip"] is False  # never trigger the server-selection probe
    assert factory_calls[0]["timeout"] == mootdx._SOCKET_TIMEOUT_SECONDS  # a hard socket deadline


def test_mootdx_reuses_one_pinned_server_across_funds(monkeypatch) -> None:
    # The pin is cached: a second fund hits the same host, not a fresh selection per call.
    factory_calls: list[dict] = []
    _install_fake_mootdx(
        monkeypatch, bars=_FakeBars({"2026-06-05": 0.98}), calls=[], factory_calls=factory_calls
    )
    mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z")
    mootdx.fetch_live("510300", fetched_at="2026-06-05T22:00:00Z")
    assert [c["server"] for c in factory_calls] == [("127.0.0.1", 7709), ("127.0.0.1", 7709)]


def test_mootdx_disables_the_librarys_auto_retry(monkeypatch) -> None:
    # mootdx hardcodes auto_retry=True; tdxpy then loops reconnect+resend against a half-dead host,
    # multiplying the deadline. We own retries at the resilience boundary, so the library's is off.
    clients: list = []
    _install_fake_mootdx(
        monkeypatch, bars=_FakeBars({"2026-06-05": 0.98}), calls=[], clients=clients
    )
    mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z")
    assert clients[0].client.auto_retry is False


def test_mootdx_closes_the_client(monkeypatch) -> None:
    closes: list = []
    _install_fake_mootdx(
        monkeypatch, bars=_FakeBars({"2026-06-05": 0.98}), calls=[], closes=closes
    )
    mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z")
    assert closes == [True]  # the per-call client is closed, not leaked


def test_mootdx_raises_on_a_silent_server_and_repins(monkeypatch) -> None:
    # The real client returns an EMPTY frame on a dead server — indistinguishable from a delisted
    # code — so the leg detects death via a liveness probe (a falsy security count) and raises, so
    # _with_retries re-picks and _live_or_empty degrades; the dead pin drops so the next rotates.
    _install_fake_mootdx(monkeypatch, bars=_FakeBars({}), calls=[], dead=True)
    with pytest.raises(IngestError):
        mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z")
    assert mootdx._server is None  # dropped the dead pin
    assert mootdx._server_index == 1  # advanced to the next candidate host


def test_mootdx_rotates_to_the_next_host_after_a_failure(monkeypatch) -> None:
    # After a failure drops the pin, the next pick is the next host in the list, not the same one.
    _install_fake_mootdx(monkeypatch, bars=_FakeBars({}), calls=[], dead=True)
    with pytest.raises(IngestError):
        mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z")
    assert mootdx._pinned_server() == ("127.0.0.2", 7709)  # HQ_HOSTS[1], the rotation target


def test_mootdx_empty_frame_on_a_live_server_is_a_delisting_not_a_failure(monkeypatch) -> None:
    # A live server (liveness probe passes) that returns an empty frame is a delisted/unknown code:
    # degrade quietly to [] and do NOT drop the pin — only a dead server rotates.
    _install_fake_mootdx(monkeypatch, bars=_FakeBars({}), calls=[])  # dead=False (live)
    assert mootdx.fetch_live("159915", fetched_at="2026-06-05T22:00:00Z") == []
    assert mootdx._server == ("127.0.0.1", 7709)  # pin kept — a live server is not rotated away


def _src(source: str, nav: float, *, as_of: str = "2026-06-05") -> list[Reading]:
    return [Reading(series="prices", key="561010", as_of=as_of, fetched_at="t",
                    payload={"nav": nav, "source": source})]


def _price(nav: float, *, as_of: str = "2026-06-05") -> list[Reading]:
    return _src("akshare", nav, as_of=as_of)  # AkShare is the priority/canonical source


def test_select_reconciled_trusts_primary_when_sources_agree() -> None:
    chosen = prices.select_reconciled([_price(1.920), _src("baostock", 1.921)])
    assert chosen[0].payload["nav"] == 1.920  # within tolerance → keep the priority AkShare read
    assert "divergence" not in chosen[0].payload  # agreement leaves no quality flag


def test_select_reconciled_falls_back_when_primary_blocked() -> None:
    chosen = prices.select_reconciled([[], _src("baostock", 1.92)])
    assert chosen[0].payload["nav"] == 1.92  # AkShare unavailable → substitute Baostock


def test_select_reconciled_keeps_a_lone_source_without_cross_check() -> None:
    chosen = prices.select_reconciled([_price(1.92), []])
    assert chosen[0].payload["nav"] == 1.92  # only one source → nothing to corroborate against


def test_select_reconciled_flags_divergence_but_continues() -> None:
    # Two sources, a material same-day disagreement must NOT kill the run (anti-fragility). Keep the
    # priority value, but flag the reading with the disagreeing peer NAV for review.
    chosen = prices.select_reconciled([_price(1.92), _src("baostock", 2.50)])
    assert len(chosen) == 1
    assert chosen[0].payload["nav"] == 1.92  # priority value retained
    assert chosen[0].payload["divergence"] == 2.50  # the unreconciled peer NAV, recorded not fatal


def test_select_reconciled_default_tolerance_is_half_a_percent() -> None:
    # The default band is the SEC/CSSF NAV-error baseline (0.5%), not the looser 1%: a 0.4% gap
    # corroborates, a 0.8% gap is flagged.
    within = prices.select_reconciled([_price(1.0), _src("baostock", 1.004)])
    flagged = prices.select_reconciled([_price(1.0), _src("baostock", 1.008)])
    assert "divergence" not in within[0].payload  # 0.4% corroborates
    assert flagged[0].payload["divergence"] == 1.008  # 0.8% is flagged


def test_select_reconciled_skips_cross_check_across_different_days() -> None:
    # A stale-but-working Baostock read (an earlier session) must not be cross-checked against a
    # fresh AkShare read — a normal day-over-day move would otherwise spuriously flag.
    chosen = prices.select_reconciled(
        [_price(1.92, as_of="2026-06-05"), _src("baostock", 2.50, as_of="2026-06-04")]
    )
    assert chosen[0].payload["nav"] == 1.92  # only the fresh read is in the cohort, no false flag
    assert "divergence" not in chosen[0].payload


def test_select_reconciled_takes_the_median_of_three() -> None:
    # Three sources → the median is the consensus, robust to a single bad source: a wild Mootdx
    # read can't poison the value, and it is flagged as the divergent peer.
    chosen = prices.select_reconciled(
        [_price(1.000), _src("baostock", 1.001), _src("mootdx", 1.50)]
    )
    assert chosen[0].payload["nav"] == 1.001  # the median, not dragged toward the 1.50 outlier
    assert chosen[0].payload["divergence"] == 1.50  # the most-divergent peer, flagged


def test_select_reconciled_median_rejects_a_bad_primary() -> None:
    # The whole point of three sources: even when the PRIORITY source (AkShare) is the bad one, the
    # two agreeing sources win the median — AkShare's number does not survive.
    chosen = prices.select_reconciled(
        [_price(99.0), _src("baostock", 1.0), _src("mootdx", 1.0)]
    )
    assert chosen[0].payload["nav"] == 1.0  # consensus, not the bad AkShare 99.0
    assert chosen[0].payload["source"] != "akshare"  # carried by an agreeing source
    assert chosen[0].payload["divergence"] == 99.0


def test_select_reconciled_three_agree_no_flag() -> None:
    chosen = prices.select_reconciled(
        [_price(1.000), _src("baostock", 1.001), _src("mootdx", 1.002)]
    )
    assert "divergence" not in chosen[0].payload  # all within tolerance → clean


