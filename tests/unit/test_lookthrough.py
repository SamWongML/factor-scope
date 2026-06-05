"""Unit tests for the deterministic, exact, point-in-time look-through (spec §05).

The motivating question: "B is falling — who else of mine holds it, and my total look-through
weight?" These tests pin the *logic* (exact funds + weighted sum + point-in-time + the shared-only
connection rule) against a tiny in-memory ``GraphStore`` fake, so they stay pure and fast. The real
on-disk DuckDB backend's durability is covered by ``tests/integration/test_graph_store.py``.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from factor_scope.graph import Edge, Holding, build_connections, look_through

pytestmark = pytest.mark.unit


class FakeGraph:
    """A dict-free, list-backed ``GraphStore`` that does the same point-in-time read as DuckDB."""

    def __init__(self, edges: Iterable[Edge]) -> None:
        self._edges: list[Edge] = list(edges)

    def add_edges(self, edges: Iterable[Edge]) -> int:
        new = list(edges)
        self._edges += new
        return len(new)

    @staticmethod
    def _pit(candidates: list[Edge], as_of: str) -> list[Edge]:
        best: dict[tuple[str, str], Edge] = {}
        for e in candidates:
            if e.as_of <= as_of:
                key = (e.fund, e.security)
                if key not in best or e.as_of > best[key].as_of:
                    best[key] = e
        return list(best.values())

    def funds_holding(self, security: str, as_of: str) -> list[Edge]:
        hits = self._pit([e for e in self._edges if e.security == security], as_of)
        return sorted(hits, key=lambda e: e.fund)

    def securities_of(self, fund: str, as_of: str) -> list[Edge]:
        hits = self._pit([e for e in self._edges if e.fund == fund], as_of)
        return sorted(hits, key=lambda e: e.security)

    def count(self) -> int:
        return len(self._edges)


Q1 = "2026-03-31"
Q2 = "2026-06-30"

_BOOK = [
    Holding(code="F1", name="Fund One", weight=0.6),
    Holding(code="F2", name="Fund Two", weight=0.4),
]


def _two_fund_graph() -> FakeGraph:
    return FakeGraph(
        [
            Edge(fund="F1", security="S1", weight=0.10, as_of=Q1, source="fund_holdings"),
            Edge(fund="F2", security="S1", weight=0.05, as_of=Q1, source="fund_holdings"),
            Edge(fund="F1", security="S2", weight=0.20, as_of=Q1, source="fund_holdings"),
        ]
    )


def test_lookthrough_returns_exact_funds_and_weighted_sum() -> None:
    lt = look_through(_two_fund_graph(), "S1", Q2, _BOOK)
    assert lt.funds == ["F1", "F2"]
    # Σ (holding weight in fund × my portfolio weight in fund): 0.10*0.6 + 0.05*0.4.
    assert lt.lookthrough_wt == pytest.approx(0.08)


def test_lookthrough_ignores_funds_outside_my_book() -> None:
    graph = _two_fund_graph()
    graph.add_edges([Edge(fund="OTHER", security="S1", weight=0.9, as_of=Q1, source="x")])
    lt = look_through(graph, "S1", Q2, _BOOK)
    assert lt.funds == ["F1", "F2"]  # OTHER is not on my lists → excluded
    assert lt.lookthrough_wt == pytest.approx(0.08)


def test_lookthrough_is_point_in_time() -> None:
    graph = _two_fund_graph()
    graph.add_edges([Edge(fund="F3", security="S1", weight=0.07, as_of=Q2, source="fund_holdings")])
    book = [*_BOOK, Holding(code="F3", name="Fund Three", weight=0.0)]
    # As of just after Q1, the Q2 disclosure is not yet knowable.
    early = look_through(graph, "S1", "2026-04-01", book)
    assert early.funds == ["F1", "F2"]
    # As of after Q2, F3 is visible (but contributes 0 weight — nothing held yet).
    late = look_through(graph, "S1", "2026-07-01", book)
    assert late.funds == ["F1", "F2", "F3"]
    assert late.lookthrough_wt == pytest.approx(0.08)


def test_build_connections_surfaces_only_shared_names() -> None:
    conns, flag = build_connections(
        _two_fund_graph(), "F1", Q2, _BOOK, down_securities={"S1"}
    )
    assert flag is True
    assert len(conns) == 1  # S2 is held only by F1 → no overlap → not surfaced
    c = conns[0]
    assert c.shared.startswith("S1")
    assert "↓" in c.shared  # S1 is marked falling
    assert c.also_in == ["Fund Two"]  # the *other* fund of mine holding it
    assert c.lookthrough_wt == pytest.approx(0.08)


def test_build_connections_no_overlap_is_empty_and_unflagged() -> None:
    graph = FakeGraph(
        [Edge(fund="F1", security="S2", weight=0.20, as_of=Q1, source="fund_holdings")]
    )
    conns, flag = build_connections(graph, "F1", Q2, _BOOK, down_securities=set())
    assert conns == []
    assert flag is False


def test_build_connections_omits_arrow_when_not_falling() -> None:
    conns, _ = build_connections(_two_fund_graph(), "F1", Q2, _BOOK, down_securities=set())
    assert conns[0].shared == "S1"  # no ↓ when the name is not flagged falling
