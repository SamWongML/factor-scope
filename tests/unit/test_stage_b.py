"""Unit tests for the emerging funnel's Stage B — screen the theme's funds to a top 3.

Stage B scores each candidate fund on a *fixed* scorecard (the same criteria every time, with
constant economic weights — never tuned to returns) and ranks them. Overlap-with-core reuses the
look-through: a candidate that just repeats names my book already holds is a leveraged
repeat, not diversification, so high overlap shrinks its score and can drop it out of the top 3.
"""

from __future__ import annotations

from datetime import date

import pytest

from factor_scope.emerging.stage_a import CROWD_VETO
from factor_scope.emerging.stage_b import (
    AUM_FLOOR,
    LAUNCH_SEASONING_DAYS,
    PE_VETO_PCTILE,
    RUN_UP_VETO,
    WEIGHTS,
    Candidate,
    coarse_filter,
    overlap_with_core,
    run_up,
    score_fund,
    screen_funds,
    veto_funds,
)
from factor_scope.graph import Edge, LadybugGraphStore
from factor_scope.graph.lookthrough import Holding

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"


def _graph() -> LadybugGraphStore:
    """A book (561010 holds 中际旭创) plus candidate funds; one candidate overlaps the book."""

    graph = LadybugGraphStore(":memory:")
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
        crowding=0.20,
        as_of="2026-05-31",
    )
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def test_weights_sum_to_one() -> None:
    # The fixed-weight combination is only a convex average if the weights sum to 1.0; a future
    # re-balance that breaks this would silently distort every total, so pin the invariant here.
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_coarse_filter_drops_funds_below_the_liquidity_floor() -> None:
    # The candidate-generation gate: a fund too thin to trade (closure/illiquidity risk) is dropped
    # before the scorecard runs, while one at the floor survives. Order is preserved.
    thin = _candidate("THIN", aum=AUM_FLOOR - 1.0)
    ok = _candidate("OK", aum=AUM_FLOOR)
    big = _candidate("BIG", aum=80.0)
    survivors = coarse_filter([thin, ok, big])
    assert [c.code for c in survivors] == ["OK", "BIG"]


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
        "crowding",
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


def test_crowded_fund_ranks_below_an_otherwise_equal_uncrowded_fund() -> None:
    graph, book = _graph(), _book()
    # Funds A and C neither overlap my core, so the only difference is crowding. A crowded theme is
    # a crash-risk gauge — size down, don't chase — so it scores lower and ranks below.
    uncrowded = score_fund(_candidate("A", crowding=0.10), graph, AS_OF, book)
    crowded = score_fund(_candidate("C", crowding=0.90), graph, AS_OF, book)
    assert crowded.subscores["crowding"] < uncrowded.subscores["crowding"]
    assert crowded.total < uncrowded.total
    pair = [_candidate("C", crowding=0.90), _candidate("A", crowding=0.10)]
    ranked = screen_funds(pair, graph, AS_OF, book, top_n=3)
    assert [s.candidate.code for s in ranked] == ["A", "C"]


def test_run_up_needs_a_quarter_of_history() -> None:
    # Fewer NAVs than the session floor → no read at all (degrade, never a spurious veto).
    assert run_up([1.0 + 0.01 * i for i in range(59)]) is None


def test_run_up_reads_at_exactly_the_session_floor() -> None:
    # Exactly the floor (60 NAVs) is enough for a read — the gate is `<`, not `<=`.
    assert run_up([1.0] * 59 + [1.5]) == pytest.approx(0.5)


def test_run_up_degrades_on_a_non_positive_base_nav() -> None:
    # A zero NAV print is bad data, not a return — degrade to no read, never raise.
    assert run_up([0.0] * 61 + [1.0]) is None


def test_run_up_reads_the_available_window_when_history_is_short() -> None:
    # 80 NAVs → the read spans all 79 sessions available (down to the floor, up to the window).
    navs = [1.0] * 79 + [1.5]
    assert run_up(navs) == pytest.approx(0.5)


def test_run_up_reads_exactly_the_run_up_window_when_history_is_long() -> None:
    # 200 NAVs → exactly the trailing 120-session return; the older 79 sessions are ignored.
    navs = [9.9] * 79 + [1.0] + [1.0] * 119 + [1.6]
    assert run_up(navs) == pytest.approx(0.6)


def test_overheated_fund_is_vetoed_with_an_auditable_reason() -> None:
    # The Ben-David conjunction: the basket ran up AND is in its own top-5% valuation.
    hot = _candidate("HOT", run_up=0.60, pe_pctile=0.96)
    cool = _candidate("COOL", run_up=0.05, pe_pctile=0.50)
    kept, vetoed = veto_funds([hot, cool], AS_OF)
    assert [c.code for c in kept] == ["COOL"]
    assert len(vetoed) == 1
    assert vetoed[0].guardrail == "overheated"
    assert vetoed[0].candidate.code == "HOT"
    for fragment in ("0.60", "0.96", f"{RUN_UP_VETO:.2f}", f"{PE_VETO_PCTILE:.2f}", AS_OF):
        assert fragment in vetoed[0].reason


def test_a_run_up_alone_is_not_vetoed() -> None:
    # A veto needs both positive signals — a run-up with no valuation read is kept.
    kept, vetoed = veto_funds([_candidate("R", run_up=0.80, pe_pctile=None)], AS_OF)
    assert [c.code for c in kept] == ["R"]
    assert vetoed == []


def test_extreme_valuation_alone_is_not_vetoed() -> None:
    # Extreme PE with a *falling* price is not the launch-at-peak basket — extreme valuation
    # already caps via the emerging gate; it never removes on its own.
    kept, vetoed = veto_funds([_candidate("V", run_up=-0.20, pe_pctile=0.97)], AS_OF)
    assert [c.code for c in kept] == ["V"]
    assert vetoed == []


def test_a_young_fund_on_a_crowded_theme_is_vetoed_launch_at_peak() -> None:
    # Providers launch specialized products at the attention peak: a fund younger than two
    # disclosure quarters riding an already-crowded theme is that product.
    young = _candidate("Y", inception="2026-03-07", crowding=0.75)
    kept, vetoed = veto_funds([young], AS_OF)
    assert kept == []
    assert vetoed[0].guardrail == "launch_at_peak"
    assert "2026-03-07" in vetoed[0].reason
    assert "0.75" in vetoed[0].reason


def test_a_young_fund_on_a_quiet_theme_is_kept() -> None:
    kept, vetoed = veto_funds([_candidate("Q", inception="2026-03-07", crowding=0.20)], AS_OF)
    assert [c.code for c in kept] == ["Q"]
    assert vetoed == []


def test_a_future_dated_inception_never_vetoes() -> None:
    # A launch date after the run date is a point-in-time-impossible disclosure — bad data, not
    # positive evidence — so it degrades to no age read, exactly like an unparseable one.
    ghost = _candidate("G", inception="2026-12-01", crowding=0.90)
    kept, vetoed = veto_funds([ghost], AS_OF)
    assert [c.code for c in kept] == ["G"]
    assert vetoed == []


def test_missing_guardrail_data_never_vetoes() -> None:
    # Degrade, never raise: with no inception, run-up, or PE read there is no positive evidence.
    bare = _candidate("BARE", crowding=0.90)
    odd = _candidate("ODD", inception="not-a-date", crowding=0.90)
    kept, vetoed = veto_funds([bare, odd], AS_OF)
    assert [c.code for c in kept] == ["BARE", "ODD"]
    assert vetoed == []


def test_veto_thresholds_are_exact() -> None:
    # run_up == RUN_UP_VETO with an extreme PE → vetoed (at the threshold is in).
    at_run_up = _candidate("RU", run_up=RUN_UP_VETO, pe_pctile=PE_VETO_PCTILE)
    kept, vetoed = veto_funds([at_run_up], AS_OF)
    assert kept == [] and vetoed[0].guardrail == "overheated"
    # Age of exactly the seasoning window → kept (the veto needs a *younger* fund).
    seasoned = _candidate("SEA", inception="2025-12-07", crowding=0.90)
    assert (date.fromisoformat(AS_OF) - date.fromisoformat("2025-12-07")).days == (
        LAUNCH_SEASONING_DAYS
    )
    kept, vetoed = veto_funds([seasoned], AS_OF)
    assert [c.code for c in kept] == ["SEA"] and vetoed == []
    # Theme crowding exactly at the veto line with a young fund → vetoed.
    at_crowd = _candidate("CR", inception="2026-03-07", crowding=0.70)
    kept, vetoed = veto_funds([at_crowd], AS_OF)
    assert kept == [] and vetoed[0].guardrail == "launch_at_peak"


def test_just_below_every_veto_line_is_kept() -> None:
    # The kept side of each threshold, one notch under the line — a silently loosened constant
    # (the at-the-line tests only catch a *tightened* one) fails here.
    near_run_up = _candidate("NR", run_up=RUN_UP_VETO - 0.01, pe_pctile=PE_VETO_PCTILE)
    near_pe = _candidate("NP", run_up=RUN_UP_VETO, pe_pctile=PE_VETO_PCTILE - 0.01)
    near_crowd = _candidate("NC", inception="2026-03-07", crowding=CROWD_VETO - 0.01)
    kept, vetoed = veto_funds([near_run_up, near_pe, near_crowd], AS_OF)
    assert [c.code for c in kept] == ["NR", "NP", "NC"]
    assert vetoed == []


def test_screen_orders_by_total_then_code() -> None:
    graph, book = _graph(), _book()
    # Two identical clean funds tie on score → ordered by code (deterministic).
    candidates = [_candidate("Z"), _candidate("A")]
    ranked = screen_funds(candidates, graph, AS_OF, book, top_n=3)
    assert [s.candidate.code for s in ranked] == ["A", "Z"]
