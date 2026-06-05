"""Unit tests for the fixture parsers of the remaining L1 adapters."""

import pytest

from factor_scope.ingest import edgar, fred, fund_holdings, prices
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
