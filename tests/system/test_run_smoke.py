"""System test — the 'nothing is broken in the session' gate for Phase 0.

End-to-end: invoke the `factor-scope run` entrypoint over the bundled fixtures and assert it
produces a schema-valid dashboard.json, deterministically, with the expected lists populated.
This test must stay green at every phase boundary.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.contract import Dashboard, ListName
from factor_scope.pipeline import build_dashboard

pytestmark = pytest.mark.system

runner = CliRunner()


def test_run_entrypoint_emits_valid_dashboard(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output

    data = json.loads(out.read_text(encoding="utf-8"))
    # Re-validate the written artifact against the contract — the file IS the deliverable.
    dash = Dashboard.model_validate(data)
    assert dash.as_of == "2026-06-05"
    assert len(dash.by_list(ListName.HOLDINGS)) == 2
    assert len(dash.by_list(ListName.WATCHLIST)) == 1
    assert len(dash.by_list(ListName.EMERGING)) == 1


def test_fixtures_run_is_deterministic() -> None:
    cfg = Config()
    first = build_dashboard(cfg).model_dump_json(indent=2)
    second = build_dashboard(cfg).model_dump_json(indent=2)
    assert first == second  # byte-for-byte reproducible (golden-file friendly)
