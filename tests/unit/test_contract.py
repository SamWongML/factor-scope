"""Unit tests for the dashboard.json contract — the spine every layer targets."""

import json

import pytest

from factor_scope.contract import (
    BullBearIndex,
    Dashboard,
    DashboardItem,
    FactorState,
    GateState,
    Lean,
    ListName,
    RubricScore,
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


def test_bull_bear_index_defaults_and_bounds() -> None:
    # The debate's descriptive decomposition: two case strengths + net, with the swap residual and
    # rubric defaulting empty (an under-construction item carries no index at all).
    idx = BullBearIndex(bull=2.0, bear=0.5, net=1.5)
    assert idx.order_residual == 0.0
    assert idx.rubric == []
    # Strengths are magnitudes (≥ 0); a negative case strength is a programming error.
    with pytest.raises(ValueError):
        BullBearIndex(bull=-0.1, bear=0.0, net=-0.1)


def test_rubric_score_is_bounded() -> None:
    ok = RubricScore(criterion="pure-play conviction", score=0.8)
    assert ok.score == 0.8
    with pytest.raises(ValueError):
        RubricScore(criterion="valuation", score=1.4)
    with pytest.raises(ValueError):
        RubricScore(criterion="valuation", score=-0.1)


def test_item_index_defaults_none_and_roundtrips() -> None:
    plain = DashboardItem(item="储能ETF", list=ListName.EMERGING)
    assert plain.index is None
    scored = DashboardItem(
        item="储能ETF",
        list=ListName.EMERGING,
        index=BullBearIndex(
            bull=1.0,
            bear=2.0,
            net=-1.0,
            order_residual=0.04,
            rubric=[RubricScore(criterion="valuation", score=0.3)],
        ),
    )
    restored = DashboardItem.model_validate_json(scored.model_dump_json())
    assert restored == scored


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
    # The per-product bull/bear index is part of the artifact contract.
    assert "BullBearIndex" in schema["$defs"]
    assert "index" in schema["$defs"]["DashboardItem"]["properties"]
