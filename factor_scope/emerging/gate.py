"""The emerging-list trend gate — a thin-history relaxation of the 200-day cap.

A brand-new fund has no 200-day MA, so the standard trend gate reads ``unknown`` and the digest
abstains — which would bury every genuinely new theme fund. For the emerging list only, when the
trend is *unknown for lack of history* we fall back to a liquidity + age + valuation read: a liquid,
long-enough, not-overvalued new fund opens; anything else is capped. A *known* trend (open or
capped) is always authoritative, so a fund already below its 200-day MA stays capped — the one hard
cap is never relaxed away. The constants carry economic meaning, never tuned to P&L.
"""

from __future__ import annotations

from factor_scope.contract import Band, GateState
from factor_scope.emerging.stage_b import AUM_FLOOR, Candidate
from factor_scope.factors.battery import FactorContext, compute_gate, valuation
from factor_scope.factors.window import price_navs

# A new fund needs at least a quarter of sessions before its liquidity/age read is trusted; below
# this it is too green to open even when liquid. (The upper bound is the 200-day MA the gate uses.)
_MIN_EMERGING_AGE = 60


def emerging_gate(ctx: FactorContext, candidate: Candidate) -> GateState:
    """The trend gate for an emerging candidate; relaxes only a thin-history *unknown* trend."""

    gate = compute_gate(ctx)
    if gate is not GateState.UNKNOWN:
        return gate  # a known open/capped trend is authoritative — never relaxed
    liquid = candidate.aum >= AUM_FLOOR
    old_enough = len(price_navs(ctx.store, ctx.code, ctx.as_of)) >= _MIN_EMERGING_AGE
    val = valuation(ctx)
    overvalued = val.valid and val.level is Band.EXTREME_HIGH  # missing/invalid PE ≠ bad
    return GateState.OPEN if liquid and old_enough and not overvalued else GateState.CAPPED


__all__ = ["emerging_gate"]
