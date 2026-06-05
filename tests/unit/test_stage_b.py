"""Unit tests for the emerging funnel's Stage B — screen the theme's funds to a top 3 (spec §07).

Stage B scores each candidate fund on a *fixed* scorecard (the same criteria every time, with
constant economic weights — never tuned to returns) and ranks them. Overlap-with-core reuses the
Phase-3 §05 look-through: a candidate that just repeats names my book already holds is a leveraged
repeat, not diversification, so high overlap shrinks its score and can drop it out of the top 3.
"""

from __future__ import annotations

import pytest

from factor_scope.emerging.stage_b import Candidate, overlap_with_core, score_fund, screen_funds
from factor_scope.graph import DuckDBGraphStore, Edge
from factor_scope.graph.lookthrough import Holding

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"


def _graph() -> DuckDBGraphStore:
    """A book (561010 holds 中际旭创) plus candidate funds; one candidate overlaps the book."""

    graph = DuckDBGraphStore(":memory:")
    graph.add_edges(
        [
            # my core fund holds 中际旭创
            Edge(fund="561010", security="中际旭创", weight=0.10, as_of="2026-03-31", source="t"),
            # clean candidate — no overlap with my core
            Edge(fund="A", security="宁德时代", weight=0.16, as_of="2026-03-31", source="t"),
            Edge(fund="A", security="阳光电源", weight=0.10, as_of="2026-03-31", source="t"),
            # overlapping candidate — holds 中际旭创 (a name my book already owns)
            Edge(fund="B", security="中际旭创", weight=0.15, as_of="2026-03-31", source="t"),
            Edge(fund="B", security="宁德时代", weight=0.10, as_of="2026-03-31", source="t"),
        ]
    )
    return graph


def _book() -> list[Holding]:
    return [Holding(code="561010", name="光通信ETF", weight=1.0)]


def _candidate(code: str, **overrides: object) -> Candidate:
    base: dict[str, object] = dict(
        theme="储能",
        code=code,
        name=f"fund-{code}",
        methodology=0.85,
        fee=0.005,
        aum=60.0,
        tracking_error=0.010,
        top10_weight=0.55,
        as_of="2026-05-31",
    )
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def test_overlap_with_core_counts_only_names_my_book_already_holds() -> None:
    graph, book = _graph(), _book()
    # Candidate B holds 中际旭创 (mine) + 宁德时代 (not mine) → overlap is the 中际旭创 weight only.
    overlap, names = overlap_with_core(graph, "B", AS_OF, book)
    assert overlap == pytest.approx(0.15)
    assert names == ["中际旭创"]
    # Candidate A overlaps nothing my book holds.
    overlap_a, names_a = overlap_with_core(graph, "A", AS_OF, book)
    assert overlap_a == pytest.approx(0.0)
    assert names_a == []


def test_score_is_deterministic_and_in_unit_range() -> None:
    graph, book = _graph(), _book()
    first = score_fund(_candidate("A"), graph, AS_OF, book)
    second = score_fund(_candidate("A"), graph, AS_OF, book)
    assert first.total == second.total
    assert 0.0 <= first.total <= 1.0
    assert set(first.subscores) == {
        "methodology",
        "overlap",
        "cost",
        "liquidity",
        "tracking",
        "concentration",
    }


def test_high_overlap_shrinks_the_score() -> None:
    graph, book = _graph(), _book()
    # Same fund profile; only the overlap differs (A clean, B repeats my core).
    clean = score_fund(_candidate("A"), graph, AS_OF, book)
    overlapping = score_fund(_candidate("B"), graph, AS_OF, book)
    assert overlapping.overlap > clean.overlap
    assert overlapping.subscores["overlap"] < clean.subscores["overlap"]
    assert overlapping.total < clean.total


def test_high_overlap_drops_a_fund_out_of_the_top_three() -> None:
    graph, book = _graph(), _book()
    # B has the *best* methodology but heavily overlaps the core; with 4 candidates it should be
    # demoted below the three clean funds and drop out of the top 3.
    candidates = [
        _candidate("A", methodology=0.80),
        _candidate("C", methodology=0.78),
        _candidate("D", methodology=0.76),
        _candidate("B", methodology=0.95),  # great fund, but a leveraged repeat of my core
    ]
    top3 = screen_funds(candidates, graph, AS_OF, book, top_n=3)
    codes = [s.candidate.code for s in top3]
    assert len(top3) == 3
    assert "B" not in codes  # overlap dropped the otherwise-strongest fund
    # Ranked by total, descending, with a deterministic code tie-break.
    totals = [s.total for s in top3]
    assert totals == sorted(totals, reverse=True)


def test_screen_orders_by_total_then_code() -> None:
    graph, book = _graph(), _book()
    # Two identical clean funds tie on score → ordered by code (deterministic).
    candidates = [_candidate("Z"), _candidate("A")]
    ranked = screen_funds(candidates, graph, AS_OF, book, top_n=3)
    assert [s.candidate.code for s in ranked] == ["A", "Z"]
