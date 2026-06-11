"""System test — the run artifact carries the emerging funnel's re-ranked top-3.

End-to-end over the bundled fixtures: a cleared theme (储能) runs the three-stage funnel —
candidate generation + Stage-B ranking to a finalist pool, then the cheap-LLM re-rank (the offline
deterministic stand-in) to the top 3 in the ``emerging`` list, each with the Stage-A clearance + the
Stage-B one-page comparison (methodology / fee / AUM / tracking / top-10 / overlap-with-core). The
candidate that heavily overlaps my core (光储龙头ETF holds 中际旭创, a name my book already owns) is
dropped by the look-through. The digest then leans over the shortlist; the capped fund is never
bullish. Deterministic, schema-valid.
"""

import json

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.config import Config
from factor_scope.contract import Dashboard, LeanAction, ListName
from factor_scope.digest import DigestInput
from factor_scope.pipeline import build_dashboard

pytestmark = pytest.mark.system

runner = CliRunner()


def test_run_emerging_list_is_a_screened_top_three(tmp_path) -> None:
    out = tmp_path / "dashboard.json"
    result = runner.invoke(app, ["run", "--output", str(out)])
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate(json.loads(out.read_text(encoding="utf-8")))
    emerging = dash.by_list(ListName.EMERGING)
    assert len(emerging) <= 3  # the re-rank emits at most a top 3 per cleared theme
    assert len(emerging) == 3  # the cleared theme fills it

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


def test_emerging_briefs_carry_funnel_near_misses(monkeypatch) -> None:
    # The finalists just below the funnel cut reach the seats as veto-only context. Observe the real
    # pipeline by spying on the digest: only emerging briefs carry near-misses, and the funnel
    # actually surfaces at least one (储能 screened 4 candidates into a top-3 → a 4th below it).
    import factor_scope.pipeline as pipeline

    real_digest = pipeline.digest_item
    briefs: list[DigestInput] = []

    def _spy(provider, brief, **kwargs):
        briefs.append(brief)
        return real_digest(provider, brief, **kwargs)

    monkeypatch.setattr(pipeline, "digest_item", _spy)
    build_dashboard(Config())

    with_near = [b for b in briefs if b.near_misses]
    assert with_near, "the funnel surfaced no near-misses to the seats"
    # Near-misses are the emerging funnel's veto context — never attached to a core holding/watch.
    assert all(b.list_name is ListName.EMERGING for b in with_near)
    assert all(line.startswith("#") for b in with_near for line in b.near_misses)


def test_overheated_fund_is_vetoed_with_an_auditable_reason() -> None:
    # 新储能ETF (562990) maps to 储能 but ran up ~60% in its first months on a basket at its own
    # top-of-history PE — the Ben-David launch-at-peak product. The guardrail vetoes it before the
    # scorecard, and every surviving emerging item carries the dated reason for the morning review.
    dash = build_dashboard(Config())
    emerging = dash.by_list(ListName.EMERGING)
    assert "新储能ETF" not in {it.item for it in emerging}
    for item in emerging:
        veto = next(e for e in item.evidence if e.src == "emerging:veto")
        assert "新储能ETF" in veto.one_line and "562990" in veto.one_line
        assert "overheated" in veto.one_line


def test_hype_theme_is_stopped_late_stage_before_any_fund() -> None:
    # 固态电池 clears the raw signal gate but is crowded (0.80) with acceleration that only just
    # cleared its floor (0.45) — a cresting wave. The late-stage veto stops the theme in Stage A,
    # so no second shortlist appears and nothing in the artifact argues over it.
    dash = build_dashboard(Config())
    emerging = dash.by_list(ListName.EMERGING)
    assert len(emerging) == 3  # still only the 储能 top-3
    for item in emerging:
        assert all("固态电池" not in e.one_line for e in item.evidence)


def test_delisted_fund_never_becomes_a_candidate(tmp_path) -> None:
    # 退市光伏ETF (159999) holds 宁德时代 and would overlap the 储能 constituents, but it delisted
    # on 2025-12-31 — before the run date — so the survivorship-aware universe excludes it from the
    # mapping, while the young-but-listed 562990 *is* mapped (and vetoed downstream, not here).
    from factor_scope.pipeline import ingest
    from factor_scope.store import DuckDBStore

    cfg = Config(store_path=tmp_path / "store.duckdb", graph_path=tmp_path / "graph.duckdb")
    ingest(cfg)
    store = DuckDBStore(tmp_path / "store.duckdb")
    try:
        keys = {r.key for r in store.read_as_of("theme_map", "2026-06-05")}
    finally:
        store.close()
    assert not any(key.endswith(":159999") for key in keys)
    assert any(key.endswith(":562990") for key in keys)


def test_a_later_delisting_disclosure_removes_a_mapped_fund(tmp_path) -> None:
    # The mapping is append-only: the 储能:561160 row was frozen while the fund was listed and is
    # never rewritten, so when a later universe disclosure marks 561160 delisted the funnel must
    # re-check membership at the run's as_of — a stale mapping row must not resurrect a dead fund.
    from factor_scope.pipeline import ingest
    from factor_scope.store import DuckDBStore, Reading

    cfg = Config(store_path=tmp_path / "store.duckdb", graph_path=tmp_path / "graph.duckdb")
    ingest(cfg)
    store = DuckDBStore(tmp_path / "store.duckdb")
    try:
        row = next(r for r in store.read_as_of("fund_universe", "2026-06-05") if r.key == "561160")
        store.append(
            [
                Reading(
                    series="fund_universe",
                    key="561160",
                    as_of="2026-06-05",
                    fetched_at="2026-06-05T23:00:00Z",
                    payload={**row.payload, "delisting": "2026-06-05"},
                )
            ]
        )
    finally:
        store.close()
    emerging = build_dashboard(cfg).by_list(ListName.EMERGING)
    assert emerging  # the theme still shortlists its surviving funds
    assert "储能ETF" not in {it.item for it in emerging}


def test_emerging_run_is_deterministic() -> None:
    cfg = Config()
    first = build_dashboard(cfg).model_dump_json(indent=2)
    second = build_dashboard(cfg).model_dump_json(indent=2)
    assert first == second
