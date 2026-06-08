"""The connection-graph store — a durable, on-disk, point-in-time holdings graph.

The look-through is exact set arithmetic over quarterly holdings snapshots. The backend is
**LadybugDB**: an embedded, on-disk, openCypher graph database. The
``(:Fund)-[:HOLDS {weight, as_of}]->(:Security)`` graph is therefore stored graph-natively —
durable on disk, offline, deterministic, and point-in-time at query time (the latest disclosure
as-of the query date, the same latest-as-of read the readings store makes). The
:class:`GraphStore` ``Protocol`` keeps the engine swappable; nothing here is an in-memory graph
rebuilt each run.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict

from factor_scope.store import PointInTimeStore

__all__ = ["Edge", "GraphStore", "LadybugGraphStore", "build_graph_from_store"]


class Edge(BaseModel):
    """One ``HOLDS`` edge: a fund holds a security at a weight, as of a disclosure date."""

    model_config = ConfigDict(frozen=True)

    fund: str  # the holder (HOLDS source) — a fund/ETF code on my lists
    security: str  # the held name (HOLDS target)
    weight: float  # the security's weight in the fund (0..1) at ``as_of``
    as_of: str  # ISO disclosure date (point-in-time; quarterly snapshots)
    source: str  # the feed this edge came from, e.g. "fund_holdings"


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


_SCHEMA = (
    "CREATE NODE TABLE IF NOT EXISTS Fund(code STRING, PRIMARY KEY(code))",
    "CREATE NODE TABLE IF NOT EXISTS Security(code STRING, PRIMARY KEY(code))",
    "CREATE REL TABLE IF NOT EXISTS HOLDS("
    "FROM Fund TO Security, weight DOUBLE, as_of STRING, source STRING)",
)

def _latest_read(returned: str) -> str:
    """Cypher for the latest HOLDS disclosure per holder as-of ``$as_of``, anchored on ``$code``.

    HOLDS always points Fund→Security; ``returned`` (``"Fund"`` or ``"Security"``) is the side we
    list, the other side is pinned by ``$code``. Per holder we take ``max(as_of) <= $as_of``
    (``as_of`` is a zero-padded ISO date, so lexical max is the chronological latest), then the max
    weight at that date, then the max source at that weight — so the returned ``(weight, source)``
    is always one *real* disclosure (never a per-column mix), and identical re-ingested edges
    collapse to one row. The graph-native equivalent of the readings store's latest-as-of read; an
    earlier query date never sees a later disclosure, and nothing rewrites an edge.
    """

    def hop(edge: str, *, first: bool) -> str:
        node = f"(n:{returned})" if first else "(n)"  # ``n`` keeps its label only when first bound
        anchor = "(:Security {code: $code})" if returned == "Fund" else "(:Fund {code: $code})"
        left, right = (node, anchor) if returned == "Fund" else (anchor, node)
        return f"{left}-[{edge}:HOLDS]->{right}"

    return (
        f"MATCH {hop('h', first=True)} WHERE h.as_of <= $as_of "
        "WITH n, max(h.as_of) AS latest "
        f"MATCH {hop('e', first=False)} WHERE e.as_of = latest "
        "WITH n, latest, max(e.weight) AS weight "
        f"MATCH {hop('g', first=False)} WHERE g.as_of = latest AND g.weight = weight "
        "RETURN n.code, weight, latest, max(g.source) ORDER BY n.code"
    )


_FUNDS_HOLDING = _latest_read("Fund")
_SECURITIES_OF = _latest_read("Security")


class LadybugGraphStore:
    """A LadybugDB-backed :class:`GraphStore`. ``path=":memory:"`` for an ephemeral graph."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        import ladybug  # lazy: the `store` extra is only needed when a graph is opened

        self._path = str(path)
        db_path = "" if self._path == ":memory:" else self._path  # "" is LadybugDB's in-memory db
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = ladybug.Database(db_path)
        self._con = ladybug.Connection(self._db)
        for stmt in _SCHEMA:
            self._con.execute(stmt)

    def add_edges(self, edges: Iterable[Edge]) -> int:
        rows = [e.model_dump() for e in edges]
        if not rows:
            return 0
        # Split write: MERGE the (idempotent) nodes, then CREATE one edge per row. A single UNWIND
        # that MERGEs the nodes and CREATEs the edge together collapses same-(fund, security) rows
        # into one edge — fatal for append-only re-ingest, where duplicate disclosures must survive.
        self._con.execute("UNWIND $rows AS r MERGE (:Fund {code: r.fund})", {"rows": rows})
        self._con.execute("UNWIND $rows AS r MERGE (:Security {code: r.security})", {"rows": rows})
        self._con.execute(
            "UNWIND $rows AS r MATCH (f:Fund {code: r.fund}), (s:Security {code: r.security}) "
            "CREATE (f)-[:HOLDS {weight: r.weight, as_of: r.as_of, source: r.source}]->(s)",
            {"rows": rows},
        )
        return len(rows)

    def funds_holding(self, security: str, as_of: str) -> list[Edge]:
        rows = self._read(_FUNDS_HOLDING, {"code": security, "as_of": as_of})
        return [
            Edge(fund=fund, security=security, weight=weight, as_of=ao, source=src)
            for fund, weight, ao, src in rows
        ]

    def securities_of(self, fund: str, as_of: str) -> list[Edge]:
        rows = self._read(_SECURITIES_OF, {"code": fund, "as_of": as_of})
        return [
            Edge(fund=fund, security=security, weight=weight, as_of=ao, source=src)
            for security, weight, ao, src in rows
        ]

    def count(self) -> int:
        return int(self._read("MATCH ()-[h:HOLDS]->() RETURN count(h)")[0][0])

    def _read(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        result = self._con.execute(cypher, parameters=params)
        assert not isinstance(result, list)  # a single-statement query returns exactly one result
        return cast("list[list[Any]]", result.get_all())  # positional rows (default, not dict mode)

    def close(self) -> None:
        self._con.close()
        self._db.close()

    def __enter__(self) -> LadybugGraphStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def build_graph_from_store(graph: GraphStore, store: PointInTimeStore) -> int:
    """Materialise the HOLDS graph from the weighted holdings readings (no LLM).

    Reads the *full* append-only history (every quarter's disclosure) so the graph keeps its own
    point-in-time read at query time — an earlier snapshot never sees a later disclosure. Both feeds
    of weighted fund/ETF holdings become edges: CN ``fund_holdings`` and US N-PORT ``edgar`` rows
    (which carry a ``weight``); 13F manager positions carry ``shares`` not a weight and are skipped.
    Returns the number of edges added.
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
    edges += [
        Edge(
            fund=str(r.payload["filer"]),
            security=str(r.payload["holding"]),
            weight=float(r.payload["weight"]),
            as_of=r.as_of,
            source="edgar",
        )
        for r in store.history("edgar")
        if "weight" in r.payload  # N-PORT fund/ETF holdings; 13F (shares-only) is not a graph edge
    ]
    return graph.add_edges(edges)
