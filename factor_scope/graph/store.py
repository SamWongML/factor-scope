"""The connection-graph store (L2) — a durable, on-disk, point-in-time holdings graph (spec §05).

The §05 look-through is exact set arithmetic over quarterly holdings snapshots, so the default
backend materialises the ``(:Fund)-[:HOLDS {weight, as_of}]->(:Security)`` graph as an append-only
edge table in DuckDB (decision D8): durable on disk, offline, deterministic, and point-in-time at
query time (the same ``QUALIFY`` latest-as-of pattern as the readings store). The
:class:`GraphStore` ``Protocol`` keeps the engine swappable (a graph-native Kùzu / Neo4j is the
production swap); nothing here is an in-memory graph rebuilt each run.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from factor_scope.store import PointInTimeStore

__all__ = ["DuckDBGraphStore", "Edge", "GraphStore", "build_graph_from_store"]


class Edge(BaseModel):
    """One ``HOLDS`` edge: a fund holds a security at a weight, as of a disclosure date."""

    model_config = ConfigDict(frozen=True)

    fund: str  # the holder (HOLDS source) — a fund/ETF code on my lists
    security: str  # the held name (HOLDS target)
    weight: float  # the security's weight in the fund (0..1) at ``as_of``
    as_of: str  # ISO disclosure date (point-in-time; quarterly snapshots)
    source: str  # the feed this edge came from, e.g. "fund_holdings"
    rel: str = "HOLDS"  # the edge kind; EXPOSED_TO (security→driver/theme) lands in a later phase


@runtime_checkable
class GraphStore(Protocol):
    """The swappable graph contract. Append-only; reads are point-in-time."""

    def add_edges(self, edges: Iterable[Edge]) -> int:
        """Append edges; return how many were written. Never updates or deletes."""

    def funds_holding(self, security: str, as_of: str) -> list[Edge]:
        """The HOLDS edges into ``security`` as of the date — latest per fund, ordered by fund."""

    def securities_of(self, fund: str, as_of: str) -> list[Edge]:
        """The HOLDS edges out of ``fund`` as of the date — latest per security, by security."""

    def count(self) -> int:
        """Number of edges in the graph."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS edges (
    fund VARCHAR NOT NULL,
    security VARCHAR NOT NULL,
    rel VARCHAR NOT NULL,
    weight DOUBLE NOT NULL,
    as_of VARCHAR NOT NULL,
    source VARCHAR NOT NULL
);
"""

_COLS = "fund, security, rel, weight, as_of, source"


class DuckDBGraphStore:
    """A DuckDB-backed :class:`GraphStore`. ``path=":memory:"`` for an ephemeral graph."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        import duckdb  # lazy: the `store` extra is only needed when a graph is opened

        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(self._path)
        self._con.execute(_SCHEMA)

    def add_edges(self, edges: Iterable[Edge]) -> int:
        rows = [(e.fund, e.security, e.rel, e.weight, e.as_of, e.source) for e in edges]
        if not rows:
            return 0
        self._con.executemany(f"INSERT INTO edges ({_COLS}) VALUES (?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def funds_holding(self, security: str, as_of: str) -> list[Edge]:
        query = f"""
        SELECT {_COLS} FROM edges
        WHERE rel = 'HOLDS' AND security = ? AND as_of <= ?
        QUALIFY row_number() OVER (PARTITION BY fund ORDER BY as_of DESC) = 1
        ORDER BY fund
        """
        return [self._to_edge(r) for r in self._con.execute(query, [security, as_of]).fetchall()]

    def securities_of(self, fund: str, as_of: str) -> list[Edge]:
        query = f"""
        SELECT {_COLS} FROM edges
        WHERE rel = 'HOLDS' AND fund = ? AND as_of <= ?
        QUALIFY row_number() OVER (PARTITION BY security ORDER BY as_of DESC) = 1
        ORDER BY security
        """
        return [self._to_edge(r) for r in self._con.execute(query, [fund, as_of]).fetchall()]

    def count(self) -> int:
        row = self._con.execute("SELECT count(*) FROM edges").fetchone()
        return int(row[0]) if row is not None else 0

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> DuckDBGraphStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _to_edge(row: tuple[Any, ...]) -> Edge:
        fund, security, rel, weight, as_of, source = row
        return Edge(
            fund=fund, security=security, rel=rel, weight=weight, as_of=as_of, source=source
        )


def build_graph_from_store(graph: GraphStore, store: PointInTimeStore) -> int:
    """Materialise the HOLDS graph straight from the ``fund_holdings`` readings (no LLM, spec §05).

    Reads the *full* append-only history (every quarter's disclosure) so the graph keeps its own
    point-in-time read at query time — an earlier snapshot never sees a later disclosure. Returns
    the number of edges added.
    """

    edges = [
        Edge(
            fund=str(r.payload["fund"]),
            security=str(r.payload["holding"]),
            weight=float(r.payload["weight"]),
            as_of=r.as_of,
            source="fund_holdings",
        )
        for r in store.history("fund_holdings")
    ]
    return graph.add_edges(edges)
