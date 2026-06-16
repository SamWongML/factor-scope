"""Emerging funnel — wire the three stages into a per-theme shortlist.

The cheap→expensive cascade: qualify each industry (Stage A); only for a cleared theme
generate + rank its candidate funds to a finalist pool on the fixed Stage-B scorecard (a coarse
liquidity filter, the anti-hype guardrails, then the graded screen); then spend a cheap-LLM re-rank
only on those finalists to land the top 3 (Stage 3). The output is a :class:`Shortlist` per cleared
theme — the one-page comparison the digest then argues bull/bear over, promoting at most one — and
it carries each guardrail veto with its dated reason, so an excluded fund stays auditable.
Deterministic: themes are processed in name order and the offline re-rank read needs no network.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from factor_scope.emerging.shortlist import NEAR_MISS_N, TOP_N, RankedFund, Reranker, rerank
from factor_scope.emerging.stage_a import StageAResult, Theme, qualify_theme
from factor_scope.emerging.stage_b import (
    FINALISTS,
    Candidate,
    FundVeto,
    coarse_filter,
    screen_funds,
    veto_funds,
)
from factor_scope.graph.lookthrough import Holding
from factor_scope.graph.store import GraphStore

__all__ = ["Shortlist", "group_by_theme", "run_funnel"]


@dataclass(frozen=True)
class Shortlist:
    """A cleared theme and its re-ranked top-3 funds (the funnel's per-theme output).

    ``near_misses`` are the finalists just below the cut (ranks ``n+1…``) — veto-only context for
    the seats, never promotable; the funnel and gate stay deterministic, so they can never surface.
    ``vetoed`` are the funds the anti-hype guardrails removed before the scorecard ran, each with
    its dated reason — never ranked, never promotable, kept only so the exclusion is auditable.
    """

    theme: str
    as_of: str  # the theme's research date (point-in-time)
    stage_a: StageAResult
    n_candidates: int  # how many candidate funds were screened (for "rank #k of n")
    funds: list[RankedFund]
    near_misses: list[RankedFund] = field(default_factory=list)
    vetoed: list[FundVeto] = field(default_factory=list)


def group_by_theme(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    """Bucket candidate funds by their theme name (deterministic insertion order preserved)."""

    by_theme: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_theme[candidate.theme].append(candidate)
    return by_theme


def run_funnel(
    themes: list[Theme],
    candidates: list[Candidate],
    graph: GraphStore,
    as_of: str,
    book: list[Holding],
    reranker: Reranker,
    *,
    finalists: int = FINALISTS,
    top_n: int = TOP_N,
) -> list[Shortlist]:
    """Qualify each theme, then rank → re-rank its funds to a top ``n``.

    For a cleared theme: a coarse liquidity filter generates the candidate set, the anti-hype
    guardrails veto the launch-at-peak products, the fixed Stage-B scorecard ranks the survivors
    to ``finalists``, and the cheap-LLM re-rank narrows those to the top ``n``. Only cleared
    themes with at least one surviving fund yield a shortlist. Themes are processed in name order
    so the emerging list is deterministic regardless of fixture row order.
    """

    by_theme = group_by_theme(candidates)
    shortlists: list[Shortlist] = []
    for theme in sorted(themes, key=lambda t: t.name):
        result = qualify_theme(theme)
        if not result.passed:
            continue
        generated = coarse_filter(by_theme.get(theme.name, []))
        if not generated:
            continue  # cleared but no investable wrapper survives the coarse filter
        kept, vetoed = veto_funds(generated, as_of)
        if not kept:
            continue  # every wrapper is a launch-at-peak product — nothing investable survives

        screened = screen_funds(kept, graph, as_of, book, top_n=finalists)
        promoted = rerank(reranker, theme.name, screened, as_of, top_n=top_n, near_n=NEAR_MISS_N)
        funds = [r for r in promoted if r.rank <= top_n]
        if not funds:
            continue
        shortlists.append(
            Shortlist(
                theme=theme.name,
                as_of=theme.as_of,
                stage_a=result,
                n_candidates=len(kept),
                funds=funds,
                near_misses=[r for r in promoted if r.rank > top_n],
                vetoed=vetoed,
            )
        )
    return shortlists
