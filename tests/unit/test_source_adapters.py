"""Unit tests for the fixture parsers of the remaining L1 adapters."""

import sys
import types

import pytest

from factor_scope.ingest import edgar, fred, fund_holdings, prices, theme_funds, themes
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit


def test_prices_reads_as_of_per_row() -> None:
    text = "code,as_of,nav\n561010,2026-06-05,1.92\n561160,2026-06-04,0.98\n"
    readings = prices.parse(text, fetched_at="2026-06-05T22:00:00Z")
    assert readings[0].series == "prices"
    assert readings[0].as_of == "2026-06-05"  # the price's own date, not the run date
    assert readings[1].as_of == "2026-06-04"
    assert readings[0].payload == {"nav": 1.92}


def test_prices_rejects_bad_nav() -> None:
    with pytest.raises(IngestError, match="nav"):
        prices.parse("code,as_of,nav\n561010,2026-06-05,n/a\n", fetched_at="x")


def test_fund_holdings_keys_each_edge() -> None:
    text = "fund,as_of,holding,weight\n561010,2026-03-31,中际旭创,0.094\n"
    readings = fund_holdings.parse(text, fetched_at="x")
    assert readings[0].key == "561010/中际旭创"  # one PIT key per (fund, holding) edge
    assert readings[0].payload == {"fund": "561010", "holding": "中际旭创", "weight": 0.094}


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


def _install_fake_edgar(monkeypatch, *, requested: list[str]) -> None:
    """Inject a network-free ``edgar`` module that records the requested form."""

    def get_filings(form: str) -> _FakeFilings:
        requested.append(form)
        if form == "13F-HR":
            obj: object = _Fake13FObj([{"Ticker": "COHR", "SharesPrnAmount": 1_250_000}])
        else:  # NPORT-P
            obj = _FakeNportObj([{"name": "APPLE INC", "balance": 500.0}])
        return _FakeFilings(_FakeFiling(obj))

    class _FakeCompany:
        def __init__(self, cik: str) -> None:
            self.cik = cik

        def get_filings(self, form: str) -> _FakeFilings:
            return get_filings(form)

    module = types.ModuleType("edgar")
    module.Company = _FakeCompany  # type: ignore[attr-defined]
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
    assert readings[0].payload == {"filer": "0000102909", "holding": "APPLE INC", "shares": 500.0}


def test_themes_keys_by_name_and_coerces_flags() -> None:
    text = (
        "theme,as_of,acceleration,base_level,breadth,crowding,"
        "broad_adoption,path_to_profit,fad_resistant,lead_chain,wrapper_exists\n"
        "储能,2026-05-31,0.62,0.30,6,0.35,1,1,1,1,0\n"
    )
    readings = themes.parse(text, fetched_at="x")
    assert readings[0].series == "themes"
    assert readings[0].key == "储能"
    assert readings[0].as_of == "2026-05-31"  # the research date, not the run date
    assert readings[0].payload["breadth"] == 6  # parsed as an int
    assert readings[0].payload["broad_adoption"] is True
    assert readings[0].payload["wrapper_exists"] is False  # 0 → False


def test_theme_funds_keys_by_code() -> None:
    text = (
        "theme,code,name,as_of,methodology,fee,aum,tracking_error,top10_weight,crowding\n"
        "储能,561160,储能ETF,2026-05-31,0.90,0.005,82,0.010,0.55,0.30\n"
    )
    readings = theme_funds.parse(text, fetched_at="x")
    assert readings[0].series == "theme_funds"
    assert readings[0].key == "561160"
    assert readings[0].payload["theme"] == "储能"
    assert readings[0].payload["name"] == "储能ETF"
    assert readings[0].payload["fee"] == 0.005
    assert readings[0].payload["crowding"] == 0.30
