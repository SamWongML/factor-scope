"""System test — the discovery service feeds the emerging funnel end-to-end.

``factor-scope discover --offline`` clusters the bundled text corpus into candidate ``themes``
Readings (BERTrend-style: noise dropped, weak/strong kept), populates each one's durability fields
with cited evidence, and appends them to a durable store. A follow-on ``run`` over that store maps
the discovered theme to its funds and surfaces them in the emerging list — while a hyped theme that
fails the Stage-A durability gate never reaches it. Separate from the nightly, deterministic.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.contract import Dashboard, ListName
from factor_scope.store import DuckDBStore

pytestmark = pytest.mark.system

runner = CliRunner()

AS_OF = "2026-06-05"


def _discover(store_path) -> None:
    result = runner.invoke(app, ["discover", "--offline", "--store-path", str(store_path)])
    assert result.exit_code == 0, result.output


def test_discover_writes_weak_and_strong_themes_with_cited_evidence(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    _discover(store_path)

    with DuckDBStore(store_path) as store:
        themes = {r.key: r for r in store.read_as_of("themes", AS_OF)}

    # Strong + weak (emerging) themes are written; the faint topic is dropped as noise.
    assert "储能" in themes  # strong
    assert "元宇宙" in themes  # weak, but hyped — kept here, stopped later at the durability gate
    assert "光伏" not in themes  # noise — never written

    storage = themes["储能"].payload
    assert storage["signal"] == "strong"
    for field in ("broad_adoption", "path_to_profit", "fad_resistant", "lead_chain"):
        assert storage[field] is True
    # Every populated field travels with a dated, sourced one-liner — the materials the user reads.
    assert len(storage["evidence"]) == 4
    for e in storage["evidence"]:
        assert {"src", "as_of", "one_line"} <= set(e)
        assert e["one_line"] and e["as_of"]


def test_discovered_theme_surfaces_in_the_emerging_list(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    graph_path = tmp_path / "graph.ladybug"
    out = tmp_path / "dashboard.json"
    _discover(store_path)

    result = runner.invoke(
        app,
        [
            "run",
            "--offline",
            "--store-path",
            str(store_path),
            "--graph-path",
            str(graph_path),
            "-o",
            str(out),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output

    dash = Dashboard.model_validate_json(out.read_text(encoding="utf-8"))
    emerging = dash.by_list(ListName.EMERGING)
    names = {it.item for it in emerging}

    assert emerging  # the discovered theme produced a screened shortlist
    assert "储能ETF" in names  # a 储能-mapped fund, derived from the discovered constituents
    # The hyped 元宇宙 theme failed Stage A's durability gate, so none of its funds reached here.
    assert "元宇宙" not in {it.item for it in emerging}
    for item in emerging:
        srcs = {e.src for e in item.evidence}
        assert {"emerging:stage_a", "emerging:stage_b"} <= srcs


def test_discovery_is_deterministic(tmp_path) -> None:
    first_path = tmp_path / "first.duckdb"
    second_path = tmp_path / "second.duckdb"
    _discover(first_path)
    _discover(second_path)

    with DuckDBStore(first_path) as a, DuckDBStore(second_path) as b:
        first = [r.model_dump() for r in a.read_as_of("themes", AS_OF)]
        second = [r.model_dump() for r in b.read_as_of("themes", AS_OF)]
    assert first == second
