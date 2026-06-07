"""Rank-against-own-history banding.

A reading becomes a *state* by ranking it against its own history into a logic-based band. The
cut-points are **constants chosen for economic meaning** — the tails (top/bottom 5%) are extremes,
the shoulders (next 20%) are high/low, the middle half is neutral — and are never tuned to P&L.
That is the whole point of "states, not a composite": nothing here is fitted.
"""

from __future__ import annotations

from collections.abc import Sequence

from factor_scope.contract import Band

# Constant percentile cut-points. Economic meaning, not optimisation: 5/25/75/95.
_CUTS: tuple[tuple[float, Band], ...] = (
    (0.05, Band.EXTREME_LOW),
    (0.25, Band.LOW),
    (0.75, Band.NEUTRAL),
    (0.95, Band.HIGH),
)


def percentile_rank(value: float, sample: Sequence[float]) -> float:
    """The mid-rank percentile of ``value`` within ``sample`` (0..1).

    Mid-rank (count below + half the ties) so a flat run of equal values lands at the centre of
    its band rather than at an edge. Values outside the sample clamp to 0.0 / 1.0.
    """

    n = len(sample)
    if n == 0:
        raise ValueError("percentile_rank: empty sample")
    below = sum(1 for x in sample if x < value)
    ties = sum(1 for x in sample if x == value)
    return (below + 0.5 * ties) / n


def rank_to_band(pct: float) -> Band:
    """Map a percentile (0..1) to its constant-cut-point :class:`Band`."""

    for threshold, band in _CUTS:
        if pct < threshold:
            return band
    return Band.EXTREME_HIGH


__all__ = ["percentile_rank", "rank_to_band"]
