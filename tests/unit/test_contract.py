"""Unit tests for the dashboard.json contract — the spine every layer targets."""

import json

import pytest

from factor_scope.contract import (
    Dashboard,
    DashboardItem,
    FactorState,
    GateState,
    Lean,
    ListName,
    dashboard_json_schema,
)

pytestmark = pytest.mark.unit


def test_minimal_item_is_valid() -> None:
    item = DashboardItem(item="optical-module ETF", list=ListName.HOLDINGS)
    assert item.list_name is ListName.HOLDINGS
    # Defaults keep an under-construction item valid: no states/leans yet.
    assert item.states == []
    assert item.connections == []
    assert item.evidence == []
    assert item.lean is None
    assert item.gate is GateState.UNKNOWN


def test_factor_state_band_and_validity() -> None:
    s = FactorState(
        factor="reversal",
        level="extreme_high",
        direction="reversal-down risk",
        evidence="ran up 40% in 12 sessions vs own history",
    )
    assert s.valid is True  # missing != bad; defaults to valid
    broken = FactorState(factor="demand", level="neutral", direction="n/a", valid=False)
    assert broken.valid is False


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValueError):
        Lean(action="trim", confidence=1.4, text="Trim")
    with pytest.raises(ValueError):
        Lean(action="trim", confidence=-0.1, text="Trim")
    ok = Lean(action="trim", confidence=0.55, text="Trim / low-conviction")
    assert ok.confidence == 0.55


def test_dashboard_roundtrips_through_json() -> None:
    dash = Dashboard(
        as_of="2026-06-05",
        generated_at="2026-06-05T22:00:00Z",
        snapshot_id="snap-abc123",
        items=[DashboardItem(item="通信ETF", list=ListName.WATCHLIST)],
    )
    blob = dash.model_dump_json()
    restored = Dashboard.model_validate_json(blob)
    assert restored == dash
    # And it is plain JSON (no exotic types leak into the artifact).
    json.loads(blob)


def test_json_schema_exports() -> None:
    schema = dashboard_json_schema()
    assert schema["title"] == "Dashboard"
    assert "items" in schema["properties"]
