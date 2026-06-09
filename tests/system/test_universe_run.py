"""System test — ``ingest`` builds the full fund universe + look-through graph, deterministically.

End-to-end over the bundled fixtures: ingest fills the point-in-time store with the universe
(``fund_universe`` + ``etf_scale``) alongside the held book, and materialises the holdings graph.
Re-ingesting a fresh store reproduces the same snapshot id and the same edge count — the universe is
data engineering, and it stays byte-for-byte stable.
"""

from __future__ import annotations

import pytest

from factor_scope.config import Config
from factor_scope.graph import LadybugGraphStore
from factor_scope.pipeline import ingest
from factor_scope.store import DuckDBStore

pytestmark = pytest.mark.system

AS_OF = "2026-06-05"


def _build(tmp_path, name: str) -> tuple[str, int, int, int]:
    cfg = Config(store_path=tmp_path / f"{name}.duckdb", graph_path=tmp_path / f"{name}.ladybug")
    ingest(cfg)
    with DuckDBStore(cfg.store_path) as store:
        universe = store.read_as_of("fund_universe", AS_OF)
        scale = store.read_as_of("etf_scale", AS_OF)
        snapshot_id = store.snapshot_id(AS_OF)
    with LadybugGraphStore(cfg.graph_path) as graph:
        edges = graph.count()
    return snapshot_id, len(universe), len(scale), edges


def test_ingest_builds_universe_and_graph_deterministically(tmp_path) -> None:
    snap_a, n_universe, n_scale, edges_a = _build(tmp_path, "a")

    # The universe is broader than the held book: it carries every fund (held, theme-candidate,
    # off-exchange, and delisted) with its scorecard inputs, plus per-exchange AUM.
    assert n_universe >= 10
    assert n_scale >= 1
    assert edges_a > 0  # the holdings graph was materialised from the universe's disclosures

    snap_b, _, _, edges_b = _build(tmp_path, "b")
    assert snap_b == snap_a  # a fresh ingest reproduces the snapshot byte-for-byte
    assert edges_b == edges_a


def test_universe_keeps_an_incomplete_fund_rather_than_dropping_it(tmp_path) -> None:
    cfg = Config(store_path=tmp_path / "s.duckdb", graph_path=tmp_path / "g.ladybug")
    ingest(cfg)
    with DuckDBStore(cfg.store_path) as store:
        universe = store.read_as_of("fund_universe", AS_OF)
    flags = {r.payload["valid"] for r in universe}
    assert flags == {True, False}  # missing scorecard inputs degrade to valid=False, not dropped
