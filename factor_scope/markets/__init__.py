"""Markets — config-selected adapters behind the source protocols.

A :class:`Market` is the seam the pipeline calls to fill the store for one run; it bundles a
universe / price / theme source (see :class:`ComposedMarket`). :func:`get_market` selects one by
name. A-share is the first and only concrete adapter — no speculative multi-market code (YAGNI).
"""

from __future__ import annotations

from factor_scope.markets.base import (
    ComposedMarket,
    Market,
    PriceSource,
    ThemeSource,
    UniverseSource,
)

__all__ = [
    "ComposedMarket",
    "Market",
    "PriceSource",
    "ThemeSource",
    "UniverseSource",
    "get_market",
]


def get_market(name: str) -> Market:
    """Select a market adapter by name. ``ashare`` is the only one (the first concrete adapter)."""

    if name == "ashare":
        from factor_scope.markets.ashare import AShareMarket

        return AShareMarket()
    raise ValueError(f"unknown market: {name!r} (expected: ashare)")
