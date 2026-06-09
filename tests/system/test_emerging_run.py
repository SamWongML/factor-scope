"""System test — the run artifact carries the emerging funnel's top-3.

End-to-end over the bundled fixtures: a cleared theme (储能) produces a ranked top-3 of its
candidate funds in the ``emerging`` list, each with the Stage-A clearance + the Stage-B one-page
comparison (methodology / fee / AUM / tracking / top-10 / overlap-with-core). The candidate that
heavily overlaps my core (光储龙头ETF holds 中际旭创, a name my book already owns) is dropped from
the top 3 by the look-through. The digest then leans over the shortlist; the capped fund is
never bullish. Deterministic, schema-valid.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.contract import Dashboard, LeanAction, ListName
from factor_scope.pipeline import build_dashboard

pytestmark = pytest.mark.system

runner = CliRunner()


def test_run_emerging_list_is_a_screened_top_three(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    emerging = dash.by_list(ListName.EMERGING)
    assert len(emerging) == 3  # the cleared theme's ranked top 3

    # The overlapping candidate (光储龙头ETF) is dropped from the top 3 by overlap-with-core.
    names = {it.item for it in emerging}
    assert "光储龙头ETF" not in names

    # Each surviving fund carries the Stage-A clearance and the Stage-B comparison, ranked.
    for item in emerging:
        srcs = {e.src for e in item.evidence}
        assert {"emerging:stage_a", "emerging:stage_b"} <= srcs
        stage_b = next(e for e in item.evidence if e.src == "emerging:stage_b")
        assert "score" in stage_b.one_line and "overlap-with-core" in stage_b.one_line


def test_emerging_ranking_orders_the_shortlist_by_score(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    runner.invoke(app, ["run", "--output", str(out)])
    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    emerging = dash.by_list(ListName.EMERGING)

    ranks = [
        next(e.one_line for e in it.evidence if e.src == "emerging:stage_b") for it in emerging
    ]
    # The best-scored candidate (储能ETF: top methodology + liquidity, zero overlap) ranks #1.
    assert emerging[0].item == "储能ETF"
    assert ranks[0].startswith("rank #1/4")


def test_capped_emerging_fund_is_never_leaned_bullish() -> None:
    # 储能ETF sits below its 200-day MA → gate capped → the funnel says do-not-chase, never buy.
    dash = build_dashboard(Config())
    capped = next(it for it in dash.by_list(ListName.EMERGING) if it.item == "储能ETF")
    assert capped.lean is not None
    assert capped.lean.action is not LeanAction.BUY_EARLY


def test_emerging_candidates_are_data_derived_without_a_tagged_table() -> None:
    # No hand-tagged theme_funds.csv exists: candidates are inferred from holdings overlap, so
    # every Stage-B comparison carries a measured overlap-with-core (the look-through, not a tag).
    assert not (Config().fixtures_dir / "theme_funds.csv").exists()
    dash = build_dashboard(Config())
    emerging = dash.by_list(ListName.EMERGING)
    assert emerging  # the funnel still produces a shortlist
    for item in emerging:
        stage_b = next(e for e in item.evidence if e.src == "emerging:stage_b")
        assert "overlap-with-core" in stage_b.one_line


def test_emerging_run_is_deterministic() -> None:
    cfg = Config()
    first = build_dashboard(cfg).model_dump_json(indent=2)
    second = build_dashboard(cfg).model_dump_json(indent=2)
    assert first == second
