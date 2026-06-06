"""Unit tests for the fixture parsers of the remaining L1 adapters."""

import sys
import types

import pytest

from factor_scope.ingest import baostock, edgar, fred, fund_holdings, prices, theme_funds, themes
from factor_scope.ingest.base import IngestError
from factor_scope.store import Reading

pytestmark = pytest.mark.unit


def test_prices_reads_as_of_per_row() -> None:
    text = "code,as_of,nav\n561010,2026-06-05,1.92\n561160,2026-06-04,0.98\n"
    readings = prices.parse(text, fetched_at="2026-06-05T22:00:00Z")
    assert readings[0].series == "prices"
    assert readings[0].as_of == "2026-06-05"  # the price's own date, not the run date
    assert readings[1].as_of == "2026-06-04"
    assert readings[0].payload == {"nav": 1.92, "source": "akshare"}  # provenance on every row


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
            obj = _FakeNportObj([{"name": "APPLE INC", "pct_value": 7.5}])
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
    # pct_value (% of net assets) → fraction weight, so N-PORT holdings can be graph HOLDS edges
    assert readings[0].payload == {"filer": "0000102909", "holding": "APPLE INC", "weight": 0.075}


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


def test_baostock_fetch_live_returns_latest_nav(monkeypatch) -> None:
    calls: list[dict] = []
    _install_fake_baostock(
        monkeypatch,
        rows=[["2026-06-04", "1.90"], ["2026-06-05", "1.92"]],
        calls=calls,
    )
    readings = baostock.fetch_live("561010", fetched_at="t")
    assert calls[0]["code"] == "sh.561010"  # 5x ETF codes are Shanghai-listed
    assert readings[0].series == "prices"  # a second source for the same prices series
    assert readings[0].key == "561010"
    assert readings[0].as_of == "2026-06-05"  # the latest disclosed bar, not the run date
    # provenance: every price reading records which source it came from (§ lineage)
    assert readings[0].payload == {"nav": 1.92, "source": "baostock"}


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


def _price(nav: float, *, as_of: str = "2026-06-05") -> list[Reading]:
    return [Reading(series="prices", key="561010", as_of=as_of, fetched_at="t",
                    payload={"nav": nav, "source": "akshare"})]


def test_select_corroborated_trusts_akshare_when_sources_agree() -> None:
    chosen = prices.select_corroborated(_price(1.920), _price(1.921))
    assert chosen[0].payload["nav"] == 1.920  # within tolerance → keep the AkShare read
    assert "divergence" not in chosen[0].payload  # agreement leaves no quality flag


def test_select_corroborated_falls_back_when_akshare_blocked() -> None:
    chosen = prices.select_corroborated([], _price(1.92))
    assert chosen[0].payload["nav"] == 1.92  # AkShare unavailable → substitute Baostock


def test_select_corroborated_keeps_akshare_without_a_cross_check() -> None:
    chosen = prices.select_corroborated(_price(1.92), [])
    assert chosen[0].payload["nav"] == 1.92  # Baostock unavailable → nothing to corroborate against


def test_select_corroborated_flags_divergence_but_continues() -> None:
    # A material same-day disagreement must NOT kill the run (anti-fragility). Keep the primary
    # AkShare value, but flag the reading with the disagreeing peer NAV for review.
    chosen = prices.select_corroborated(_price(1.92), _price(2.50))
    assert len(chosen) == 1
    assert chosen[0].payload["nav"] == 1.92  # primary value retained
    assert chosen[0].payload["divergence"] == 2.50  # the unreconciled peer NAV, recorded not fatal


def test_select_corroborated_default_tolerance_is_half_a_percent() -> None:
    # The default band is the SEC/CSSF NAV-error baseline (0.5%), not the looser 1%: a 0.4% gap
    # corroborates, a 0.8% gap is flagged.
    assert "divergence" not in prices.select_corroborated(_price(1.0), _price(1.004))[0].payload
    assert prices.select_corroborated(_price(1.0), _price(1.008))[0].payload["divergence"] == 1.008


def test_select_corroborated_skips_cross_check_across_different_days() -> None:
    # A stale-but-working Baostock read (an earlier session) must not be cross-checked against a
    # fresh AkShare read — a normal day-over-day move would otherwise spuriously kill the run.
    chosen = prices.select_corroborated(
        _price(1.92, as_of="2026-06-05"), _price(2.50, as_of="2026-06-04")
    )
    assert chosen[0].payload["nav"] == 1.92  # trust the fresh AkShare read, no false conflict
    assert "divergence" not in chosen[0].payload  # different days → no cross-check, no flag


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
