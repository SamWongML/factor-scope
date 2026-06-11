"""Unit tests for the emerging funnel's Stage 3 — re-rank the finalists to a top 3.

The deterministic Stage-B scorecard ranks the candidate funds to a handful of finalists cheaply;
Stage 3 spends only a *cheap* LLM read over those few, then applies two business rules the score
does not encode — de-dup leveraged repeats, freshness — to land the top 3. The Stage-B total stays
the spine (distinct totals are never reordered), so judgment never becomes a fitted composite; the
qualitative read only decides a near-tie. The offline read is a deterministic stand-in.
"""

from __future__ import annotations

import pytest

from factor_scope.config import Config
from factor_scope.emerging.shortlist import (
    FRESHNESS_WINDOW_DAYS,
    TIE_BAND,
    FakeReranker,
    get_reranker,
    rerank,
)
from factor_scope.emerging.stage_b import Candidate, FundScore

pytestmark = pytest.mark.unit

RUN = "2026-06-05"


def _score(
    code: str,
    total: float,
    *,
    methodology: float = 0.80,
    overlap: float = 0.0,
    names: tuple[str, ...] = (),
    as_of: str = RUN,
) -> FundScore:
    candidate = Candidate(
        theme="储能",
        code=code,
        name=f"fund-{code}",
        methodology=methodology,
        fee=0.005,
        aum=60.0,
        tracking_error=0.010,
        top10_weight=0.55,
        crowding=0.20,
        as_of=as_of,
    )
    return FundScore(
        candidate=candidate, overlap=overlap, overlap_names=names, subscores={}, total=total
    )


def test_rerank_emits_at_most_top_n_ranked_one_to_n() -> None:
    finalists = [_score("A", 0.70), _score("B", 0.60), _score("C", 0.50), _score("D", 0.40)]
    top = rerank(FakeReranker(), "储能", finalists, RUN, top_n=3)
    assert [r.rank for r in top] == [1, 2, 3]
    assert [r.score.candidate.code for r in top] == ["A", "B", "C"]


def test_rerank_surfaces_near_misses_below_the_cut() -> None:
    # The next finalists below the top-n cut are kept (ranked on) as near-misses — cheap veto
    # context for the seats — never promoted into the shortlist itself.
    finalists = [
        _score(c, t) for c, t in [("A", 0.70), ("B", 0.60), ("C", 0.50), ("D", 0.40), ("E", 0.30)]
    ]
    promoted = rerank(FakeReranker(), "储能", finalists, RUN, top_n=3, near_n=2)
    assert [(r.score.candidate.code, r.rank) for r in promoted] == [
        ("A", 1),
        ("B", 2),
        ("C", 3),
        ("D", 4),
        ("E", 5),
    ]


def test_stage_b_total_is_the_spine_when_totals_separate() -> None:
    # The qualitative read favours B, but the funds are far apart on the deterministic score — the
    # re-rank must not override the scorecard, so Stage-B order stands.
    finalists = [_score("A", 0.70, methodology=0.10), _score("B", 0.60, methodology=0.99)]
    top = rerank(FakeReranker(), "储能", finalists, RUN, top_n=2)
    assert [r.score.candidate.code for r in top] == ["A", "B"]


def test_qualitative_read_breaks_a_near_tie() -> None:
    # A and B sit within one TIE_BAND on total → the cheap-LLM read (here, the measured pure-play)
    # decides, and the stronger pure-play takes #1.
    assert abs(0.700 - 0.699) < TIE_BAND
    finalists = [_score("A", 0.700, methodology=0.10), _score("B", 0.699, methodology=0.99)]
    top = rerank(FakeReranker(), "储能", finalists, RUN, top_n=2)
    assert [r.score.candidate.code for r in top] == ["B", "A"]


def test_dedup_drops_a_leveraged_repeat() -> None:
    # A and B both re-buy 中际旭创, a name already held through my core; keeping both would double
    # the same redundant bet, so the lower-ranked repeat is dropped (diversity over duplication).
    finalists = [
        _score("A", 0.70, overlap=0.15, names=("中际旭创",)),
        _score("B", 0.60, overlap=0.12, names=("中际旭创",)),
        _score("C", 0.50),
    ]
    top = rerank(FakeReranker(), "储能", finalists, RUN, top_n=3)
    codes = [r.score.candidate.code for r in top]
    assert codes == ["A", "C"]  # B dropped as a leveraged repeat → fewer than top_n survive


def test_freshness_drops_a_stale_finalist() -> None:
    stale = "2025-01-01"  # far older than FRESHNESS_WINDOW_DAYS before the run
    assert FRESHNESS_WINDOW_DAYS < 365
    finalists = [_score("A", 0.70), _score("B", 0.60, as_of=stale), _score("C", 0.50)]
    top = rerank(FakeReranker(), "储能", finalists, RUN, top_n=3)
    assert [r.score.candidate.code for r in top] == ["A", "C"]


def test_rerank_is_deterministic() -> None:
    finalists = [_score("A", 0.70), _score("B", 0.699, methodology=0.9), _score("C", 0.50)]
    first = rerank(FakeReranker(), "储能", finalists, RUN, top_n=3)
    second = rerank(FakeReranker(), "储能", finalists, RUN, top_n=3)
    assert [(r.score.candidate.code, r.rank) for r in first] == [
        (r.score.candidate.code, r.rank) for r in second
    ]


def test_fake_read_preference_is_in_unit_range_and_deterministic() -> None:
    fake = FakeReranker()
    pref = fake.read("储能", _score("A", 0.50, methodology=0.80))
    assert 0.0 <= pref <= 1.0
    assert pref == fake.read("储能", _score("A", 0.50, methodology=0.80))


def test_get_reranker_offline_is_the_deterministic_fake() -> None:
    assert isinstance(get_reranker(Config(source="fixtures")), FakeReranker)
