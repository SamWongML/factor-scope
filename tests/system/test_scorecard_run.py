"""System test — the run artifact carries the self-scoring scorecard.

End-to-end over the bundled fixtures: prior calls are scored against the committed price history and
a rolling, descriptive ``scorecard`` is attached to every item. The mirror is read-only — it never
opens a capped gate or changes a state. Deterministic, schema-valid.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.contract import Dashboard, GateState
from factor_scope.pipeline import build_dashboard

pytestmark = pytest.mark.system

runner = CliRunner()


def test_run_attaches_a_scorecard(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    assert dash.items
    for item in dash.items:
        card = item.scorecard
        assert card is not None, f"{item.item} has no scorecard"
        assert card.n >= 10  # enough resolved calls to clear the min-sample gate
        assert card.brier is not None and 0.0 <= card.brier <= 1.0
        assert card.reliability  # at least one populated confidence bucket
    # The mirror is the same book-wide calibration on every item.
    cards = [it.scorecard.model_dump_json() for it in dash.items]
    assert len(set(cards)) == 1
    # The confident "trim the winner" pattern that fights the uptrend surfaces as a weak pattern.
    weak = dash.items[0].scorecard.weak_patterns
    assert any("reversal:extreme_high" in w for w in weak)


def test_scorecard_does_not_open_a_capped_gate() -> None:
    # Build with the scorecard; the capped energy-storage ETF must stay capped — the descriptive
    # mirror can never open the hard trend gate.
    dash = build_dashboard(Config())
    capped = [it for it in dash.items if it.gate is GateState.CAPPED]
    assert capped, "expected at least one capped item in the fixture story"
    for it in capped:
        assert it.scorecard is not None  # the mirror is attached
        assert it.gate is GateState.CAPPED  # but the gate is untouched


def test_scorecard_run_is_deterministic() -> None:
    cfg = Config()
    first = build_dashboard(cfg).model_dump_json(indent=2)
    second = build_dashboard(cfg).model_dump_json(indent=2)
    assert first == second
