"""Emerging funnel — Stage B: screen a cleared theme's funds to a top 3 (spec §07).

Only for a theme that cleared Stage A. Each candidate CN fund/ETF is scored on the **same fixed
scorecard every time** — the discipline that separates selection from guessing — and ranked to a
defensible top 3 with the numbers behind it (the one-page comparison the digest can defend).

The criteria (spec §07):

* **methodology / pure-play** — a clear revenue-from-theme rule beats a loose label.
* **overlap with core** — via the Phase-3 §05 look-through: if my book already holds the theme's
  winners, a thematic fund is a *leveraged repeat*, not diversification. High overlap → shrink/skip.
* **crowding** — how crowded the fund's theme already is; a crowded theme is a crash-risk gauge —
  size down, don't chase — so a more-crowded fund scores lower.
* **cost** — total fee; small edges compound, so cheaper wins unless methodology justifies more.
* **liquidity & size** — AUM (thin funds carry closure/tracking risk).
* **tracking quality** — tracking error vs the index (are you getting the exposure you paid for?).
* **concentration** — top-10 weight (narrow can mean conviction or fragility).

Each criterion is mapped to a sub-score in ``[0, 1]`` (higher = better) against a constant ref,
then combined with **fixed economic-priority weights** (methodology and overlap are the decisive
pair). These weights are deliberate constants — never tuned to returns — so this is a transparent
*screening* scorecard, not a fitted composite of the §03 judgment factors (principle #1; see D10).
"""

from __future__ import annotations

from dataclasses import dataclass

from factor_scope.graph.lookthrough import Holding, look_through
from factor_scope.graph.store import GraphStore

__all__ = [
    "AUM_REF",
    "FEE_CAP",
    "OVERLAP_CAP",
    "TE_CAP",
    "WEIGHTS",
    "Candidate",
    "FundScore",
    "overlap_with_core",
    "score_fund",
    "screen_funds",
]

# Constant references the raw inputs are scored against (economic meaning, never tuned to P&L):
FEE_CAP = 0.015  # a fee at/above this scores 0 on cost (1.5% total)
AUM_REF = 60.0  # AUM (in 亿元) that earns a full liquidity score
TE_CAP = 0.03  # tracking error at/above this scores 0 on tracking quality
OVERLAP_CAP = 0.20  # look-through overlap at/above this scores 0 (a full leveraged repeat)

# Fixed economic-priority weights (sum to 1.0). Methodology + overlap are the decisive pair: a
# genuine, non-redundant exposure is the whole point of a satellite. NOT tuned to returns (D10).
WEIGHTS: dict[str, float] = {
    "methodology": 0.25,
    "overlap": 0.25,
    "crowding": 0.10,
    "cost": 0.15,
    "liquidity": 0.15,
    "tracking": 0.05,
    "concentration": 0.05,
}


@dataclass(frozen=True)
class Candidate:
    """One candidate fund/ETF for a theme and the fixed-scorecard inputs (point-in-time)."""

    theme: str
    code: str
    name: str
    methodology: float  # pure-play score 0..1 (clear revenue-from-theme rule → high)
    fee: float  # total management + custody fee (fraction, e.g. 0.005 = 0.5%)
    aum: float  # fund size in 亿元 (liquidity/size proxy)
    tracking_error: float  # tracking error vs the index (fraction)
    top10_weight: float  # top-10 holdings weight (concentration, 0..1)
    crowding: float  # how crowded the fund's theme is (0..1; a crash-risk gauge — higher is worse)
    as_of: str  # the research date this read was true as of


@dataclass(frozen=True)
class FundScore:
    """A candidate's fixed-scorecard sub-scores, look-through overlap, and combined total."""

    candidate: Candidate
    overlap: float  # look-through weight already held through my core (0..1)
    overlap_names: tuple[str, ...]  # the overlapping names (for the one-page comparison)
    subscores: dict[str, float]  # each criterion → 0..1 (higher = better)
    total: float  # the fixed-weight combination, 0..1


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def overlap_with_core(
    graph: GraphStore, fund_code: str, as_of: str, book: list[Holding]
) -> tuple[float, list[str]]:
    """How much of a candidate's portfolio my book already owns, via the §05 look-through.

    For each security the candidate holds, run the exact look-through against my book; a positive
    look-through weight means I already own that name through my core, so the candidate's weight in
    it is a leveraged repeat. Returns ``(total overlapping weight, sorted overlapping names)`` —
    reusing the Phase-3 set arithmetic, with no new graph logic.
    """

    overlap = 0.0
    names: list[str] = []
    for edge in graph.securities_of(fund_code, as_of):
        if look_through(graph, edge.security, as_of, book).lookthrough_wt > 0:
            overlap += edge.weight
            names.append(edge.security)
    return overlap, sorted(names)


def score_fund(
    candidate: Candidate, graph: GraphStore, as_of: str, book: list[Holding]
) -> FundScore:
    """Score one candidate on the fixed scorecard (each criterion → 0..1, then fixed-weight sum)."""

    overlap, names = overlap_with_core(graph, candidate.code, as_of, book)
    subscores = {
        "methodology": _clamp(candidate.methodology),
        "overlap": 1.0 - min(1.0, overlap / OVERLAP_CAP),
        "crowding": 1.0 - _clamp(candidate.crowding),
        "cost": 1.0 - min(1.0, candidate.fee / FEE_CAP),
        "liquidity": min(1.0, candidate.aum / AUM_REF),
        "tracking": 1.0 - min(1.0, candidate.tracking_error / TE_CAP),
        "concentration": _clamp(1.0 - candidate.top10_weight),
    }
    total = round(sum(WEIGHTS[k] * subscores[k] for k in WEIGHTS), 6)
    return FundScore(
        candidate=candidate,
        overlap=round(overlap, 6),
        overlap_names=tuple(names),
        subscores=subscores,
        total=total,
    )


def screen_funds(
    candidates: list[Candidate],
    graph: GraphStore,
    as_of: str,
    book: list[Holding],
    top_n: int = 3,
) -> list[FundScore]:
    """Score every candidate and return the top ``n`` — by total desc, then code (deterministic)."""

    scored = [score_fund(c, graph, as_of, book) for c in candidates]
    scored.sort(key=lambda s: (-s.total, s.candidate.code))
    return scored[:top_n]
