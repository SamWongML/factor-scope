"""System test — the run artifact carries a calibrated lean per item.

End-to-end over the bundled fixtures with the deterministic fake provider: every item gets a
``lean`` + ``evolution`` + ``flip_trigger`` + ``invalidation``; the capped item is never leaned
bullish; the stretched winner leans Trim; each emitted lean is logged as a falsifiable call so the
self-scoring loop scores it next run. Deterministic, schema-valid.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.contract import Dashboard, GateState, LeanAction
from factor_scope.pipeline import build_dashboard

pytestmark = pytest.mark.system

runner = CliRunner()


def test_run_attaches_a_lean_to_every_item(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    assert dash.items
    for item in dash.items:
        assert item.lean is not None, f"{item.item} has no lean"
        assert 0.0 <= item.lean.confidence <= 1.0
        assert item.lean.text
        assert item.evolution
        assert item.flip_trigger
        # An abstain makes no falsifiable claim, so it carries no invalidation; a real lean must.
        if item.lean.action is not LeanAction.ABSTAIN:
            assert item.invalidation


def test_run_attaches_a_bull_bear_index_to_every_item() -> None:
    # Every item carries the debate decomposition behind its lean: the two case strengths and
    # their net tilt. Offline the fake provider is order-invariant and scores no rubric, so the
    # residual is zero and the rubric empty — the decomposition's extra colour ships with the real
    # provider, while the structure is present on every item regardless.
    dash = build_dashboard(Config())
    assert dash.items
    for item in dash.items:
        assert item.index is not None, f"{item.item} has no index"
        idx = item.index
        assert idx.bull >= 0.0
        assert idx.bear >= 0.0
        assert idx.net == idx.bull - idx.bear
        assert idx.order_residual == 0.0  # the fake is order-invariant offline
        assert idx.rubric == []  # the fake scores no rubric offline


def test_capped_item_is_never_leaned_bullish() -> None:
    dash = build_dashboard(Config())
    capped = [it for it in dash.items if it.gate is GateState.CAPPED]
    assert capped, "expected a capped item in the fixture story"
    for it in capped:
        assert it.lean is not None
        assert it.lean.action is not LeanAction.BUY_EARLY


def test_stretched_winner_leans_trim() -> None:
    # 光通信 (561010) reads reversal extreme_high (reversal-DOWN risk) → Trim.
    dash = build_dashboard(Config())
    winner = next(it for it in dash.items if it.item.startswith("光通信"))
    assert winner.lean is not None
    assert winner.lean.action is LeanAction.TRIM


def test_emitted_leans_are_logged_as_calls(tmp_path) -> None:
    # The leans land in the point-in-time store so next run's self-scoring loop can score them.
    from factor_scope.scoring import read_calls
    from factor_scope.store import DuckDBStore

    store_path = tmp_path / "store.duckdb"
    graph_path = tmp_path / "graph.ladybug"
    cfg = Config(store_path=store_path, graph_path=graph_path)
    dash = build_dashboard(cfg)
    build_dashboard(cfg)  # re-run the same night: logging must be idempotent (no double-count)

    store = DuckDBStore(store_path)
    try:
        tonight = [c for c in read_calls(store, dash.as_of) if c.as_of == dash.as_of]
    finally:
        store.close()
    logged = {c.code for c in tonight}
    assert len(tonight) == len(dash.items)  # one call per item, made tonight (not duplicated)
    assert "561010" in logged  # the stretched winner's Trim call is recorded


def test_digest_run_is_deterministic() -> None:
    cfg = Config()
    first = build_dashboard(cfg).model_dump_json(indent=2)
    second = build_dashboard(cfg).model_dump_json(indent=2)
    assert first == second
