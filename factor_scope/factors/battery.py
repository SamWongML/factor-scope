"""The 8 descriptive factor states + the 200-day trend gate.

Each factor is a pure function ``(FactorContext) -> FactorState``: it ranks one reading against its
*own* history into a constant-cut-point :class:`~factor_scope.contract.Band`, attaches a
``direction`` (which way the band points in **risk** terms — in A-shares a stretched up-move is a
reversal-DOWN risk), a dated ``evidence`` line, and a ``valid`` flag. **No weighted composite is
ever formed.** A factor whose inputs are missing or too short returns ``valid=False`` and is
ignored downstream — missing is not the same as bad, and a factor never raises.

Four states are data-backed from the point-in-time store today (trend gate, reversal,
low-vol/drawdown, the macro dial); the other four (cross-market lead, crowding, demand, valuation)
need inputs not yet ingested, so they are emitted present-but-invalid until their sources land.
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


# --- 1. Cross-market lead (US leader → A-share chain). Needs US prices/revisions (not yet here).
def cross_market(ctx: FactorContext) -> FactorState:
    return _unavailable(
        "cross-market lead", "US leader prices/revisions not ingested"
    )


# --- 2. Reversal: short-horizon return vs own history. Up-stretch → reversal-DOWN risk.
def reversal(ctx: FactorContext) -> FactorState:
    navs = window.price_navs(ctx.store, ctx.code, ctx.as_of)
    if len(navs) < _REVERSAL_LOOKBACK * 2:
        return _unavailable("reversal", "price history too short for a reversal read")
    rets = window.horizon_returns(navs, _REVERSAL_LOOKBACK)
    current = rets[-1]
    pct = percentile_rank(current, rets)
    band = rank_to_band(pct)
    _reversal_dir = {
        Band.EXTREME_HIGH: "ran up hard → reversal-DOWN risk",
        Band.HIGH: "stretched up → reversal-DOWN risk",
        Band.LOW: "soft recent run → reversal-UP potential",
        Band.EXTREME_LOW: "sold off hard → reversal-UP potential",
    }
    direction = _reversal_dir.get(band, "no short-horizon extreme")
    return FactorState(
        factor="reversal",
        level=band,
        direction=direction,
        evidence=f"{_REVERSAL_LOOKBACK}d return {current:+.1%} (pctile {pct:.0%})",
        valid=True,
    )


# --- 3. Crowding: turnover + Amihud + ETF flow + theme PE premium. A risk gauge (not yet ingested).
def crowding(ctx: FactorContext) -> FactorState:
    return _unavailable(
        "crowding", "turnover / Amihud / flow / PE-premium not ingested"
    )


# --- 4. Demand / leading driver: revision direction of end-demand capex/orders (not yet ingested).
def demand(ctx: FactorContext) -> FactorState:
    return _unavailable("demand", "end-demand capex/orders revisions not ingested")


# --- 5. Valuation: PE/PB/PEG vs the theme's own history (fundamentals not yet ingested).
def valuation(ctx: FactorContext) -> FactorState:
    return _unavailable("valuation", "PE/PB/PEG fundamentals not ingested")


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
