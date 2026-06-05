"""System gate for Phase 2 — the run artifact carries factor states + the trend gate.

End-to-end over the bundled fixtures: every item gets a non-empty ``states[]`` bundle and a
computed ``gate``; at least one item is ``capped`` (below its 200-day MA) and at least one is
``open``. Deterministic, schema-valid. Stays green at every later phase boundary.
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


def test_run_attaches_states_and_gate(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    for item in dash.items:
        assert item.states, f"{item.item} has no states"
        assert len(item.states) == 8
        assert item.gate is not GateState.UNKNOWN

    gates = {it.gate for it in dash.items}
    assert GateState.CAPPED in gates  # the energy-storage ETF sits below its 200-day MA
    assert GateState.OPEN in gates


def test_states_run_is_deterministic() -> None:
    cfg = Config()
    first = build_dashboard(cfg).model_dump_json(indent=2)
    second = build_dashboard(cfg).model_dump_json(indent=2)
    assert first == second
