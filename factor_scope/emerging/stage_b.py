"""Emerging funnel — Stage B: screen a cleared theme's funds to a finalist pool.

Only for a theme that cleared Stage A. Stage B is the funnel's **ranking** stage: a coarse
liquidity filter first drops funds too thin to be investable (candidate generation), the anti-hype
guardrails then veto the launch-at-peak products outright, and each surviving CN fund/ETF is
scored on the **same fixed scorecard every time** — the discipline that separates selection from
guessing — and ranked to a defensible finalist pool with the numbers behind it (the one-page
comparison). Only the finalists earn the Stage-3 cheap-LLM re-rank to the top 3 (see
:mod:`~factor_scope.emerging.shortlist`).

The guardrails encode Ben-David et al. (RFS 2023): specialized ETFs lose ~30% risk-adjusted over
their first five years because providers launch them at the attention peak on overvalued
underlyings. Each veto therefore needs the **conjunction of two positive signals** — a basket that
*ran up* and is *expensive against its own history* (overheated), or a fund *younger than two
disclosure quarters* riding an *already-crowded* theme (launch-at-peak). Extreme valuation alone
never removes (it already caps via the emerging gate), and missing data never vetoes — a veto
requires positive evidence, so a thin read degrades to "kept".

The criteria:

* **methodology / pure-play** — a clear revenue-from-theme rule beats a loose label.
* **overlap with core** — via the look-through: if my book already holds the theme's
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
*screening* scorecard, not a fitted composite of the judgment factors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from factor_scope.emerging.stage_a import CROWD_VETO
from factor_scope.graph.lookthrough import Holding, look_through, overlap_with
from factor_scope.graph.store import GraphStore

__all__ = [
    "AUM_FLOOR",
    "AUM_REF",
    "FEE_CAP",
    "FINALISTS",
    "LAUNCH_SEASONING_DAYS",
    "OVERLAP_CAP",
    "PE_VETO_PCTILE",
    "RUN_UP_MIN_SESSIONS",
    "RUN_UP_VETO",
    "RUN_UP_WINDOW",
    "TE_CAP",
    "WEIGHTS",
    "Candidate",
    "FundScore",
    "FundVeto",
    "coarse_filter",
    "overlap_with_core",
    "run_up",
    "score_fund",
    "screen_funds",
    "veto_funds",
]

# Constant references the raw inputs are scored against (economic meaning, never tuned to P&L):
FEE_CAP = 0.015  # a fee at/above this scores 0 on cost (1.5% total)
AUM_REF = 60.0  # AUM (in 亿元) that earns a full liquidity score
TE_CAP = 0.03  # tracking error at/above this scores 0 on tracking quality
OVERLAP_CAP = 0.20  # look-through overlap at/above this scores 0 (a full leveraged repeat)
AUM_FLOOR = 5.0  # 亿元: a fund thinner than this carries closure/illiquidity risk → not investable
FINALISTS = 10  # the ranking stage's pool size; Stage 3 re-ranks these few to the top 3

# Anti-hype guardrail constants (economic meaning, never tuned to P&L):
RUN_UP_WINDOW = 120  # sessions (~6 months) the run-up reads — long-horizon, not the 20d reversal
RUN_UP_MIN_SESSIONS = 60  # under a quarter of NAVs there is no run-up read at all
RUN_UP_VETO = 0.50  # ≥50% in ~6 months is the run-up that precedes thematic underperformance
PE_VETO_PCTILE = 0.95  # the EXTREME_HIGH band cut: the basket's own top-5% valuation
LAUNCH_SEASONING_DAYS = 180  # two disclosure quarters; younger + a crowded theme = launch-at-peak

# Fixed economic-priority weights (sum to 1.0). Methodology + overlap are the decisive pair: a
# genuine, non-redundant exposure is the whole point of a satellite. NOT tuned to returns.
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
    inception: str | None = None  # the fund's launch date (ISO); None when undisclosed
    run_up: float | None = None  # trailing ~6-month return; None when the history is too thin
    pe_pctile: float | None = None  # latest PE vs the basket's own history; None when too few


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
    """How much of a candidate's portfolio my book already owns, via the look-through.

    For each security the candidate holds, run the exact look-through against my book; a positive
    look-through weight means I already own that name through my core, so the candidate's weight in
    it is a leveraged repeat. Returns ``(total overlapping weight, sorted overlapping names)`` —
    reusing the shared set-arithmetic primitive, with no new graph logic.
    """

    def mine(security: str) -> bool:
        return look_through(graph, security, as_of, book).lookthrough_wt > 0

    return overlap_with(graph, fund_code, as_of, mine)


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


def coarse_filter(candidates: list[Candidate], *, aum_floor: float = AUM_FLOOR) -> list[Candidate]:
    """Drop funds too thin to be investable before the scorecard runs (candidate generation).

    A hard liquidity/tradability gate — a fund below :data:`AUM_FLOOR` carries closure and
    illiquidity risk, so it never reaches the graded scorecard. This is the funnel's cheap first
    prune (order preserved), distinct from the soft ``liquidity`` sub-score that grades survivors.
    """

    return [c for c in candidates if c.aum >= aum_floor]


def run_up(navs: list[float]) -> float | None:
    """The trailing :data:`RUN_UP_WINDOW`-session simple return, or ``None`` when too thin.

    Deliberately long-horizon (~6 months) — the relative-price run-up that precedes thematic
    underperformance — distinct from the reversal factor's 20-day read. Histories between the
    session floor and the full window read over what is available.
    """

    if len(navs) < RUN_UP_MIN_SESSIONS:
        return None
    window = min(len(navs) - 1, RUN_UP_WINDOW)
    base = navs[-1 - window]
    if base <= 0:
        return None  # a non-positive NAV print is bad data, not a return — degrade, never raise
    return navs[-1] / base - 1.0


def _fund_age_days(inception: str | None, as_of: str) -> int | None:
    if not inception:
        return None
    try:
        age = (date.fromisoformat(as_of) - date.fromisoformat(inception)).days
    except ValueError:
        return None  # an unparseable disclosure is no evidence — degrade, never raise
    return age if age >= 0 else None  # a launch date after the run date is equally no evidence


@dataclass(frozen=True)
class FundVeto:
    """One fund the guardrails removed, with the dated, auditable reason."""

    candidate: Candidate
    guardrail: str  # "overheated" | "launch_at_peak"
    reason: str


def veto_funds(candidates: list[Candidate], as_of: str) -> tuple[list[Candidate], list[FundVeto]]:
    """Apply the anti-hype guardrails; return ``(kept, vetoed)`` with order preserved.

    Each veto needs the conjunction of two positive signals (see the module docstring); any
    missing input means no veto. The first guardrail tripped names the veto.
    """

    kept: list[Candidate] = []
    vetoed: list[FundVeto] = []
    for c in candidates:
        if (
            c.run_up is not None
            and c.pe_pctile is not None
            and c.run_up >= RUN_UP_VETO
            and c.pe_pctile >= PE_VETO_PCTILE
        ):
            vetoed.append(
                FundVeto(
                    candidate=c,
                    guardrail="overheated",
                    reason=(
                        f"overheated as of {as_of}: run-up {c.run_up:.2f} at/above "
                        f"{RUN_UP_VETO:.2f} and PE percentile {c.pe_pctile:.2f} at/above "
                        f"{PE_VETO_PCTILE:.2f} — the launch-at-peak basket"
                    ),
                )
            )
            continue
        age = _fund_age_days(c.inception, as_of)
        if age is not None and age < LAUNCH_SEASONING_DAYS and c.crowding >= CROWD_VETO:
            vetoed.append(
                FundVeto(
                    candidate=c,
                    guardrail="launch_at_peak",
                    reason=(
                        f"launch-at-peak as of {as_of}: launched {c.inception} "
                        f"({age}d < {LAUNCH_SEASONING_DAYS}d seasoning) into a theme already "
                        f"crowded at {c.crowding:.2f} (veto line {CROWD_VETO:.2f})"
                    ),
                )
            )
            continue
        kept.append(c)
    return kept, vetoed


def screen_funds(
    candidates: list[Candidate],
    graph: GraphStore,
    as_of: str,
    book: list[Holding],
    top_n: int,
) -> list[FundScore]:
    """Score every candidate and return the top ``n`` — by total desc, then code (deterministic)."""

    scored = [score_fund(c, graph, as_of, book) for c in candidates]
    scored.sort(key=lambda s: (-s.total, s.candidate.code))
    return scored[:top_n]
