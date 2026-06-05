"""The connection graph + deterministic look-through (L2, spec §05).

A durable, on-disk, point-in-time holdings graph (``(:Fund)-[:HOLDS{weight,as_of}]->(:Security)``)
built straight from the holdings feeds (no LLM), and the exact set-arithmetic look-through it
powers: "B is falling — who else of mine holds it, and my total look-through weight?" The
:class:`GraphStore` ``Protocol`` keeps the engine swappable (decision D8); the default backend is
:class:`DuckDBGraphStore`.
"""

from __future__ import annotations

from factor_scope.graph.lookthrough import (
    Holding,
    LookThrough,
    build_connections,
    look_through,
)
from factor_scope.graph.store import (
    DuckDBGraphStore,
    Edge,
    GraphStore,
    build_graph_from_store,
)

__all__ = [
    "DuckDBGraphStore",
    "Edge",
    "GraphStore",
    "Holding",
    "LookThrough",
    "build_connections",
    "build_graph_from_store",
    "look_through",
]
