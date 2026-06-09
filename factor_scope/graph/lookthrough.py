"""The exact, point-in-time look-through query + connection builder.

Answers the motivating question — "B is falling — who else of mine holds it, and my total
look-through weight?" — as pure set arithmetic over the
:class:`~factor_scope.graph.store.GraphStore`: the funds on my lists holding a security, and
``Σ (weight in fund × my portfolio weight in fund)``.
A connection is surfaced only when a name is shared across more than one of my funds (the
illusion-of-diversification catch); a falling name carries a ``↓`` marker.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass

from factor_scope.contract import Connection
from factor_scope.graph.store import GraphStore

__all__ = ["Holding", "LookThrough", "build_connections", "look_through", "overlap_with"]


@dataclass(frozen=True)
class Holding:
    """One fund on my lists and my portfolio weight in it (0 for a not-yet-held watch name)."""

    code: str
    name: str
    weight: float  # my portfolio weight in this fund, 0..1 (by market value)


@dataclass(frozen=True)
class LookThrough:
    """The exact look-through to one security across my book."""

    security: str
    funds: list[str]  # the fund codes of mine holding it, as of the date (sorted)
    lookthrough_wt: float  # my total look-through weight: Σ (weight in fund × my weight in fund)


def overlap_with(
    graph: GraphStore, fund_code: str, as_of: str, matches: Callable[[str], bool]
) -> tuple[float, list[str]]:
    """A fund's disclosed weight in the securities a predicate selects (point-in-time set math).

    Walk the fund's holdings as of the date and sum the weight of every security ``matches`` takes,
    returning ``(total weight, sorted matching names)``. The one set-arithmetic primitive behind
    both overlap-with-core (the predicate is "a name my book already holds") and theme overlap /
    重合度 (the predicate is "a name in the theme's reference constituents").
    """

    overlap = 0.0
    names: list[str] = []
    for edge in graph.securities_of(fund_code, as_of):
        if matches(edge.security):
            overlap += edge.weight
            names.append(edge.security)
    return overlap, sorted(names)


def look_through(
    graph: GraphStore, security: str, as_of: str, book: list[Holding]
) -> LookThrough:
    """Funds on my lists holding ``security`` (point-in-time) + my total look-through weight."""

    mine = {h.code: h for h in book}
    holders = [e for e in graph.funds_holding(security, as_of) if e.fund in mine]
    funds = [e.fund for e in holders]  # already ordered by fund from the store
    wt = sum(e.weight * mine[e.fund].weight for e in holders)
    return LookThrough(security=security, funds=funds, lookthrough_wt=wt)


def build_connections(
    graph: GraphStore,
    fund_code: str,
    as_of: str,
    book: list[Holding],
    down_securities: Collection[str],
) -> tuple[list[Connection], bool]:
    """The shared-name overlaps for one of my funds → ``connections[]`` + ``connections_flag``.

    For each security this fund holds, look through to every fund of mine holding it; surface it
    only when another of my funds shares it (the diversification illusion). The ``shared`` label
    gets a ``↓`` when the name is in ``down_securities`` (flagged falling).
    """

    names = {h.code: h.name for h in book}
    connections: list[Connection] = []
    for held in graph.securities_of(fund_code, as_of):
        lt = look_through(graph, held.security, as_of, book)
        also_in = [names[code] for code in lt.funds if code != fund_code]
        if not also_in:
            continue  # held by me only through this one fund — no overlap to surface
        arrow = " ↓" if held.security in down_securities else ""
        connections.append(
            Connection(
                shared=f"{held.security}{arrow}",
                also_in=also_in,
                lookthrough_wt=round(lt.lookthrough_wt, 6),
            )
        )
    return connections, bool(connections)
