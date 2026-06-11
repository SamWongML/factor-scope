"""Unit tests for the emerging funnel wiring — the guardrails sit between generation and ranking.

The funnel's cheap→expensive cascade gains the anti-hype veto after the coarse liquidity filter
and before the graded scorecard: a vetoed fund never reaches the finalists or the seats, and the
shortlist carries the dated veto reason so the morning review sees why each was excluded. A theme
whose funds are all vetoed yields no shortlist at all.
"""

from __future__ import annotations

import pytest

from factor_scope.emerging.funnel import run_funnel
from factor_scope.emerging.shortlist import FakeReranker
from factor_scope.emerging.stage_a import Theme
from factor_scope.emerging.stage_b import Candidate
from factor_scope.graph import LadybugGraphStore
from factor_scope.graph.lookthrough import Holding

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"


def _theme(name: str = "储能") -> Theme:
    return Theme(
        name=name,
        acceleration=0.62,
        base_level=0.30,
        breadth=6,
        crowding=0.35,
        broad_adoption=True,
        path_to_profit=True,
        fad_resistant=True,
        lead_chain=True,
        wrapper_exists=True,
        as_of="2026-05-31",
    )


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
        crowding=0.35,
        as_of="2026-05-31",
    )
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def _funnel(candidates: list[Candidate]) -> list:
    graph = LadybugGraphStore(":memory:")
    book: list[Holding] = []
    return run_funnel([_theme()], candidates, graph, AS_OF, book, FakeReranker())


def test_an_overheated_fund_is_vetoed_out_of_the_shortlist() -> None:
    sound = _candidate("561000")
    hot = _candidate("562000", run_up=0.60, pe_pctile=0.96)
    shortlists = _funnel([sound, hot])
    assert len(shortlists) == 1
    shortlist = shortlists[0]
    assert [r.score.candidate.code for r in shortlist.funds] == ["561000"]
    assert shortlist.n_candidates == 1  # the vetoed fund never counted as screened
    assert len(shortlist.vetoed) == 1
    assert shortlist.vetoed[0].candidate.code == "562000"
    assert "overheated" in shortlist.vetoed[0].reason


def test_a_theme_whose_funds_are_all_vetoed_yields_no_shortlist() -> None:
    hot = _candidate("562000", run_up=0.60, pe_pctile=0.96)
    assert _funnel([hot]) == []
