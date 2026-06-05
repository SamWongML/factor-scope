"""Unit tests for the positions adapter — the user's book → point-in-time readings."""

import pytest

from factor_scope.contract import ListName
from factor_scope.ingest import positions
from factor_scope.ingest.base import IngestError

pytestmark = pytest.mark.unit

_GOOD = (
    "code,name,cost_basis,shares,list\n"
    "561010,optical ETF,1.85,10000,holdings\n"
    "588200,chip ETF,1.05,0,watchlist\n"
)


def test_parses_rows_into_stamped_readings() -> None:
    readings = positions.parse(_GOOD, as_of="2026-06-05", fetched_at="2026-06-05T22:00:00Z")
    assert [r.key for r in readings] == ["561010", "588200"]
    first = readings[0]
    assert first.series == "positions"
    assert first.as_of == "2026-06-05"  # stamped with the run date
    assert first.fetched_at == "2026-06-05T22:00:00Z"
    assert first.payload == {
        "name": "optical ETF",
        "cost_basis": 1.85,
        "shares": 10000.0,
        "list": "holdings",
    }


def test_maps_to_contract_list_names() -> None:
    readings = positions.parse(_GOOD, as_of="2026-06-05", fetched_at="x")
    assert {r.payload["list"] for r in readings} == {ListName.HOLDINGS, ListName.WATCHLIST}


def test_rejects_unknown_list() -> None:
    bad = "code,name,cost_basis,shares,list\n561010,x,1.0,1,sleeve\n"
    with pytest.raises(IngestError, match="list"):
        positions.parse(bad, as_of="2026-06-05", fetched_at="x")


def test_rejects_non_numeric_cost_basis() -> None:
    bad = "code,name,cost_basis,shares,list\n561010,x,cheap,1,holdings\n"
    with pytest.raises(IngestError, match="cost_basis"):
        positions.parse(bad, as_of="2026-06-05", fetched_at="x")


def test_rejects_missing_column() -> None:
    bad = "code,name,cost_basis,shares\n561010,x,1.0,1\n"  # no `list`
    with pytest.raises(IngestError, match="header"):
        positions.parse(bad, as_of="2026-06-05", fetched_at="x")


def test_rejects_empty_code() -> None:
    bad = "code,name,cost_basis,shares,list\n ,x,1.0,1,holdings\n"
    with pytest.raises(IngestError, match="code"):
        positions.parse(bad, as_of="2026-06-05", fetched_at="x")
