"""System gate for Phase 1 — `ingest` (fixtures) → store → `run` yields the priced artifact.

End-to-end over the bundled fixtures: ingest fills the point-in-time store, then run reads it and
emits a schema-valid dashboard.json with the three lists, non-empty evidence, and per-item gain,
deterministically. Stays green at every later phase boundary.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.contract import Dashboard, ListName

pytestmark = pytest.mark.system

runner = CliRunner()


def test_ingest_then_run_from_durable_store(tmp_path) -> None:
    store = tmp_path / "store.duckdb"
    out = tmp_path / "dashboard.json"

    ingest = runner.invoke(app, ["ingest", "--store-path", str(store)])
    assert ingest.exit_code == 0, ingest.output

    run = runner.invoke(app, ["run", "--store-path", str(store), "--output", str(out)])
    assert run.exit_code == 0, run.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    assert dash.as_of == "2026-06-05"
    assert len(dash.by_list(ListName.HOLDINGS)) == 2
    assert len(dash.by_list(ListName.WATCHLIST)) == 1
    assert len(dash.by_list(ListName.EMERGING)) == 1

    # Every item is priced from the store: real evidence + a per-item gain.
    for item in dash.items:
        assert item.gain is not None
        assert item.evidence and "NAV" in item.evidence[0].one_line


def test_standalone_run_auto_ingests_fixtures(tmp_path) -> None:
    # No prior `ingest`: an in-memory store is auto-filled so `run` works standalone.
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output
    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    assert len(dash.items) == 4
    assert all(it.gain is not None for it in dash.items)
