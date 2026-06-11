"""The 8 descriptive factor states + the 200-day trend gate.

Each factor is a pure function ``(FactorContext) -> FactorState``: it ranks one reading against its
*own* history into a constant-cut-point :class:`~factor_scope.contract.Band`, attaches a
``direction`` (which way the band points in **risk** terms — in A-shares a stretched up-move is a
reversal-DOWN risk), a dated ``evidence`` line, and a ``valid`` flag. **No weighted composite is
ever formed.** A factor whose inputs are missing or too short returns ``valid=False`` and is
ignored downstream — missing is not the same as bad, and a factor never raises.

All eight states are data-backed from the point-in-time store: trend gate, reversal, low-vol and
crowding/valuation read a single item's own price/turnover/PE history, while the macro dial,
cross-market lead and demand are one book-wide regime each. An item with too little (or no) history
for a given factor degrades that one state to ``valid=False`` and keeps the rest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factor_scope.contract import Band, FactorState, GateState
from factor_scope.factors import window
from factor_scope.factors.bands import percentile_rank, rank_to_band
from factor_scope.store import PointInTimeStore

# Minimum history a data-backed factor needs before it will produce a band.
_MIN_TREND = 200  # the gate is a 200-day MA — below this we cannot judge the trend
_REVERSAL_LOOKBACK = 20
_VOL_WINDOW = 20
_MIN_MACRO = 12  # ~a year of monthly real-yield observations
_MIN_CROWDING = 12  # ~a fortnight of sessions before turnover ranks against its own history
_MIN_DEMAND = 8  # ~two years of quarterly end-demand revisions
_MIN_LEAD = 6  # ~6 quarters of 13F disclosures → 5 lead-chain changes to rank against
_HEAVY_TURNOVER = 0.75  # a run/sell-off on top-quartile turnover confirms the reversal read

# Which way a short-horizon return extreme points in *risk* terms (A-shares mean-revert).
_REVERSAL_DIR = {
    Band.EXTREME_HIGH: "ran up hard → reversal-DOWN risk",
    Band.HIGH: "stretched up → reversal-DOWN risk",
    Band.LOW: "soft recent run → reversal-UP potential",
    Band.EXTREME_LOW: "sold off hard → reversal-UP potential",
}


@dataclass(frozen=True)
class FactorContext:
    """What a factor needs to read one item's states, point-in-time."""

    code: str
    as_of: str
    store: PointInTimeStore


def _unavailable(factor: str, note: str) -> FactorState:
    """A present-but-ignored state: inputs missing or too short (missing ≠ bad)."""

    return FactorState(
        factor=factor, level=Band.NEUTRAL, direction="n/a", evidence=note, valid=False
    )


# --- 1. Cross-market lead: US leaders' 13F accumulation vs its own history. A book-wide lead chain.
def cross_market(ctx: FactorContext) -> FactorState:
    levels = window.lead_chain(ctx.store, ctx.as_of)
    if len(levels) < _MIN_LEAD:
        return _unavailable("cross-market lead", "US lead-chain history too short for a read")
    changes = [levels[i] - levels[i - 1] for i in range(1, len(levels))]
    current = changes[-1]
    pct = percentile_rank(current, changes)
    band = rank_to_band(pct)
    if band in (Band.HIGH, Band.EXTREME_HIGH):
        direction = "US leaders accumulated (lead-chain confirms demand)"
    elif band in (Band.LOW, Band.EXTREME_LOW):
        direction = "US leaders distributed (lead-chain rolling over → chain risk)"
    else:
        direction = "lead-chain steady"
    return FactorState(
        factor="cross-market lead",
        level=band,
        direction=direction,
        evidence=f"US lead 13F Δshares {current:+,.0f} (pctile {pct:.0%})",
        valid=True,
    )


# --- 2. Reversal: short-horizon return vs own history, confirmed by turnover / Amihud illiquidity.
def reversal(ctx: FactorContext) -> FactorState:
    navs = window.price_navs(ctx.store, ctx.code, ctx.as_of)
    if len(navs) < _REVERSAL_LOOKBACK * 2:
        return _unavailable("reversal", "price history too short for a reversal read")
    rets = window.horizon_returns(navs, _REVERSAL_LOOKBACK)
    current = rets[-1]
    pct = percentile_rank(current, rets)
    band = rank_to_band(pct)
    direction = _REVERSAL_DIR.get(band, "no short-horizon extreme")
    evidence = f"{_REVERSAL_LOOKBACK}d return {current:+.1%} (pctile {pct:.0%})"
    # The band is the return rank alone (no composite). Turnover/Amihud *qualify* it: a move on
    # heavy turnover is a confirmed exhaustion, and the Amihud illiquidity gauges how real it is.
    turns = window.turnovers(ctx.store, ctx.code, ctx.as_of)
    if len(turns) >= _MIN_CROWDING:
        turn_pct = percentile_rank(turns[-1], turns)
        heavy = turn_pct >= _HEAVY_TURNOVER
        if heavy and band in (Band.HIGH, Band.EXTREME_HIGH):
            direction = "ran up hard on heavy turnover → strong reversal-DOWN risk"
        elif heavy and band in (Band.LOW, Band.EXTREME_LOW):
            direction = "sold off on heavy turnover → reversal-UP potential"
        evidence += f"; turnover pctile {turn_pct:.0%}"
        amounts = window.traded_values(ctx.store, ctx.code, ctx.as_of)
        if amounts and amounts[-1]:
            evidence += f", Amihud {abs(current) / amounts[-1]:.2g}"
    return FactorState(factor="reversal", level=band, direction=direction, evidence=evidence)


# --- 3. Crowding: daily turnover (换手率) vs its own history. A crash-risk gauge for hot products.
def crowding(ctx: FactorContext) -> FactorState:
    turns = window.turnovers(ctx.store, ctx.code, ctx.as_of)
    if len(turns) < _MIN_CROWDING:
        return _unavailable("crowding", "turnover history too short for a crowding read")
    current = turns[-1]
    pct = percentile_rank(current, turns)
    band = rank_to_band(pct)
    if band in (Band.HIGH, Band.EXTREME_HIGH):
        direction = "crowded (turnover high vs own range → crash-risk gauge)"
    elif band in (Band.LOW, Band.EXTREME_LOW):
        direction = "quiet (turnover light vs own range)"
    else:
        direction = "normal turnover"
    return FactorState(
        factor="crowding",
        level=band,
        direction=direction,
        evidence=f"turnover {current:.2f}% (pctile {pct:.0%})",
        valid=True,
    )


# --- 4. Demand / leading driver: end-demand orders/capex revisions vs own history. Book-wide dial.
def demand(ctx: FactorContext) -> FactorState:
    revs = window.demand_revisions(ctx.store, ctx.as_of)
    if len(revs) < _MIN_DEMAND:
        return _unavailable("demand", "end-demand revision history too short for a read")
    current = revs[-1]
    pct = percentile_rank(current, revs)
    band = rank_to_band(pct)
    if band in (Band.HIGH, Band.EXTREME_HIGH):
        direction = "accelerating (end-demand revised up → demand tailwind)"
    elif band in (Band.LOW, Band.EXTREME_LOW):
        direction = "fading (end-demand revised down → demand headwind)"
    else:
        direction = "steady demand"
    return FactorState(
        factor="demand",
        level=band,
        direction=direction,
        evidence=f"end-demand revision {current:+.1%} (pctile {pct:.0%})",
        valid=True,
    )


# --- 5. Valuation: the basket's PE (市盈率) vs its own history. The anti-hype overvaluation gauge.
def valuation(ctx: FactorContext) -> FactorState:
    pes = window.valuation_pes(ctx.store, ctx.code, ctx.as_of)
    pct = window.latest_pe_percentile(pes)
    if pct is None:
        return _unavailable("valuation", "PE history too short for a valuation read")
    current = pes[-1]
    band = rank_to_band(pct)
    if band in (Band.HIGH, Band.EXTREME_HIGH):
        direction = "expensive (PE high vs own range → overvaluation risk)"
    elif band in (Band.LOW, Band.EXTREME_LOW):
        direction = "cheap (PE low vs own range)"
    else:
        direction = "fair (PE mid-range)"
    return FactorState(
        factor="valuation",
        level=band,
        direction=direction,
        evidence=f"PE {current:.1f} (pctile {pct:.0%})",
        valid=True,
    )


# --- 6. Trend gate: price vs 200-day MA + ~1y return sign. A downtrend filter — the one hard cap.
def trend_gate(ctx: FactorContext) -> FactorState:
    navs = window.price_navs(ctx.store, ctx.code, ctx.as_of)
    if len(navs) < _MIN_TREND:
        return _unavailable("trend gate", f"need ≥{_MIN_TREND}d history for the 200-day MA")
    last = navs[-1]
    ma200 = window.moving_average(navs, _MIN_TREND)
    gap = last / ma200 - 1.0
    below = last < ma200
    excess = navs[-1] / navs[0] - 1.0  # ~1y total return over the available window
    band = rank_to_band(percentile_rank(last, navs))  # where price sits in its own range
    direction = (
        f"{'downtrend' if below else 'uptrend'} (price {gap:+.0%} vs 200d-MA; ~1y {excess:+.0%})"
    )
    return FactorState(
        factor="trend gate",
        level=band,
        direction=direction,
        evidence=f"nav {last:.3f} vs 200d-MA {ma200:.3f} ({gap:+.1%})",
        valid=True,
    )


# --- 7. Low-vol / drawdown regime: realised-vol percentile + current drawdown depth.
def low_vol(ctx: FactorContext) -> FactorState:
    navs = window.price_navs(ctx.store, ctx.code, ctx.as_of)
    if len(navs) < _VOL_WINDOW * 2:
        return _unavailable("low-vol/drawdown", "price history too short for a volatility read")
    vols = window.rolling_vol(navs, _VOL_WINDOW)
    current = vols[-1]
    pct = percentile_rank(current, vols)
    band = rank_to_band(pct)
    dd = window.drawdown(navs)
    if band in (Band.HIGH, Band.EXTREME_HIGH):
        direction = "elevated volatility (stressed)"
    elif band in (Band.LOW, Band.EXTREME_LOW):
        direction = "calm (low-vol regime)"
    else:
        direction = "normal volatility"
    return FactorState(
        factor="low-vol/drawdown",
        level=band,
        direction=direction,
        evidence=f"{_VOL_WINDOW}d vol pctile {pct:.0%}; drawdown {dd:+.1%}",
        valid=True,
    )


# --- 8. Macro / liquidity dial: 10-yr real yield (DFII10) vs its own history. One book-wide regime.
def macro(ctx: FactorContext) -> FactorState:
    values = window.fred_values(ctx.store, "DFII10", ctx.as_of)
    if len(values) < _MIN_MACRO:
        return _unavailable("macro dial", "real-yield history too short for a regime read")
    current = values[-1]
    pct = percentile_rank(current, values)
    band = rank_to_band(pct)
    if band in (Band.HIGH, Band.EXTREME_HIGH):
        direction = "tight (real-yield high → liquidity headwind)"
    elif band in (Band.LOW, Band.EXTREME_LOW):
        direction = "easy (real-yield low → liquidity tailwind)"
    else:
        direction = "neutral liquidity"
    usd = window.fred_latest(ctx.store, "DTWEXBGS", ctx.as_of)
    cny = window.fred_latest(ctx.store, "DEXCHUS", ctx.as_of)
    extras = []
    if usd is not None:
        extras.append(f"USD idx {usd:g}")
    if cny is not None:
        extras.append(f"USD/CNY {cny:g}")
    tail = ("; " + "; ".join(extras)) if extras else ""
    return FactorState(
        factor="macro dial",
        level=band,
        direction=direction,
        evidence=f"10y real yield {current:.2f}% (pctile {pct:.0%}){tail}",
        valid=True,
    )


# Canonical factor order — the battery is read in this order everywhere.
FACTORS: tuple[Callable[[FactorContext], FactorState], ...] = (
    cross_market,
    reversal,
    crowding,
    demand,
    valuation,
    trend_gate,
    low_vol,
    macro,
)

FACTOR_NAMES: tuple[str, ...] = (
    "cross-market lead",
    "reversal",
    "crowding",
    "demand",
    "valuation",
    "trend gate",
    "low-vol/drawdown",
    "macro dial",
)


def compute_states(ctx: FactorContext) -> list[FactorState]:
    """The full 8-state bundle for one item, in canonical order. Never raises."""

    return [factor(ctx) for factor in FACTORS]


def compute_gate(ctx: FactorContext) -> GateState:
    """The 200-day trend gate — a hard rule. Below the MA → ``capped``; above → ``open``.

    Too little history to judge the trend → ``unknown`` (the digest treats it as blind).
    Nothing downstream may open a capped gate.
    """

    navs = window.price_navs(ctx.store, ctx.code, ctx.as_of)
    if len(navs) < _MIN_TREND:
        return GateState.UNKNOWN
    below = navs[-1] < window.moving_average(navs, _MIN_TREND)
    return GateState.CAPPED if below else GateState.OPEN


__all__ = [
    "FACTORS",
    "FACTOR_NAMES",
    "FactorContext",
    "compute_gate",
    "compute_states",
    "cross_market",
    "crowding",
    "demand",
    "low_vol",
    "macro",
    "reversal",
    "trend_gate",
    "valuation",
]
