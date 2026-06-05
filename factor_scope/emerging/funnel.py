"""Emerging funnel — wire Stage A → Stage B into a per-theme shortlist (L3, spec §07).

The two-stage funnel: qualify each industry (Stage A), and only for a cleared theme screen its
candidate funds to a top 3 (Stage B). The output is a :class:`Shortlist` per cleared theme — the
one-page comparison the digest then argues bull/bear over, promoting at most one. Deterministic:
themes are processed in name order and funds ranked by the fixed Stage-B scorecard.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from factor_scope.emerging.stage_a import StageAResult, Theme, qualify_theme
from factor_scope.emerging.stage_b import Candidate, FundScore, screen_funds
from factor_scope.graph.lookthrough import Holding
from factor_scope.graph.store import GraphStore

__all__ = ["Shortlist", "group_by_theme", "run_funnel"]


@dataclass(frozen=True)
class Shortlist:
    """A cleared theme and its ranked top-3 funds (the funnel's per-theme output)."""

    theme: str
    as_of: str  # the theme's research date (point-in-time)
    stage_a: StageAResult
    n_candidates: int  # how many candidate funds were screened (for "rank #k of n")
    funds: list[FundScore]


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
    *,
    top_n: int = 3,
) -> list[Shortlist]:
    """Qualify each theme; for those that clear Stage A, screen their funds to a top ``n``.

    Only cleared themes with at least one candidate fund yield a shortlist. Themes are processed in
    name order so the emerging list is deterministic regardless of fixture row order.
    """

    by_theme = group_by_theme(candidates)
    shortlists: list[Shortlist] = []
    for theme in sorted(themes, key=lambda t: t.name):
        result = qualify_theme(theme)
        if not result.passed:
            continue
        theme_candidates = by_theme.get(theme.name, [])
        if not theme_candidates:
            continue  # cleared but no investable wrapper actually present → nothing to screen
        funds = screen_funds(theme_candidates, graph, as_of, book, top_n=top_n)
        shortlists.append(
            Shortlist(
                theme=theme.name,
                as_of=theme.as_of,
                stage_a=result,
                n_candidates=len(theme_candidates),
                funds=funds,
            )
        )
    return shortlists
