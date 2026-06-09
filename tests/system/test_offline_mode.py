"""The CLI online/offline flip, end to end.

``run`` defaults to live sources + the real provider; ``--offline`` selects the deterministic
fixtures path. With offline off, the snapshot boundary refuses to fetch over an empty store —
which is exactly how we pin that the default is online and never silently falls back to fixtures.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.contract import Dashboard, ListName

pytestmark = pytest.mark.system

runner = CliRunner()

OFFLINE_ENV = "FACTOR_SCOPE_OFFLINE"


def test_run_offline_emits_a_deterministic_fixtures_dashboard(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--offline", "--output", str(out)])
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    assert dash.as_of == "2026-06-05"
    assert len(dash.by_list(ListName.HOLDINGS)) == 2


def test_run_online_default_refuses_to_fetch_without_a_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(OFFLINE_ENV, raising=False)
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code != 0  # live source + empty store → SnapshotError, not a fixtures run
    assert not out.exists()
