"""Unit tests for the fixture parsers of the remaining L1 adapters."""

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
