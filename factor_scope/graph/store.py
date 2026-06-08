"""The connection-graph store — a durable, on-disk, point-in-time holdings graph.

The look-through is exact set arithmetic over quarterly holdings snapshots. The backend is
**LadybugDB**: an embedded, on-disk, openCypher graph database. The
``(:Fund)-[:HOLDS {weight, as_of, valid_from, valid_to}]->(:Security)`` graph is therefore stored
graph-natively — durable on disk, offline, deterministic, and point-in-time at query time: each
edge is live only within its half-open ``[valid_from, valid_to)`` window, so the read returns the
disclosure in force on the query date and a closed position drops out (survivorship-aware). Writes
are idempotent on the disclosure identity ``(endpoints, as_of, source, valid_from)`` — re-ingesting
a night is a no-op, while a new disclosure (a new ``as_of``) is a new row — which resolves the
append-only-vs-idempotent tension at the edge level. The :class:`GraphStore` ``Protocol`` keeps the
engine swappable; nothing here is an in-memory graph rebuilt each run.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from factor_scope.store import PointInTimeStore, Reading

__all__ = ["Edge", "GraphStore", "LadybugGraphStore", "build_graph_from_store"]

OPEN_END = "9999-12-31"  # exclusive upper bound for an open-ended (still-held) holding window


class Edge(BaseModel):
    """One ``HOLDS`` edge: a fund holds a security at a weight, disclosed ``as_of`` and valid over
    the half-open window ``[valid_from, valid_to)``."""

    model_config = ConfigDict(frozen=True)

    fund: str  # the holder (HOLDS source) — a fund/ETF code on my lists
    security: str  # the held name (HOLDS target)
    weight: float  # the security's weight in the fund (0..1) at ``as_of``
    as_of: str  # ISO disclosure date (point-in-time; quarterly snapshots)
    source: str  # the feed this edge came from, e.g. "fund_holdings"
    valid_from: str = ""  # ISO start of validity; the validator fills it from as_of when omitted
    valid_to: str = OPEN_END  # exclusive ISO end of validity; ``OPEN_END`` = open-ended

    @model_validator(mode="before")
    @classmethod
    def _valid_from_defaults_to_as_of(cls, data: Any) -> Any:
        """A disclosed holding is valid from its disclosure date unless an earlier window is set."""
        if isinstance(data, dict) and data.get("valid_from") is None and "as_of" in data:
            data = {**data, "valid_from": data["as_of"]}
        return data


@runtime_checkable
class GraphStore(Protocol):
    """The swappable graph contract. Idempotent on disclosure identity; reads are point-in-time."""

    def add_edges(self, edges: Iterable[Edge]) -> int:
        """Add edges idempotently (keyed on disclosure identity); return how many were written."""

    def funds_holding(self, security: str, as_of: str) -> list[Edge]:
        """The HOLDS edges into ``security`` live at the date — latest per fund, ordered by fund."""

    def securities_of(self, fund: str, as_of: str) -> list[Edge]:
        """The HOLDS edges out of ``fund`` live at the date — latest per security, by security."""

    def count(self) -> int:
        """Number of edges in the graph."""


_SCHEMA = (
    "CREATE NODE TABLE IF NOT EXISTS Fund(code STRING, PRIMARY KEY(code))",
    "CREATE NODE TABLE IF NOT EXISTS Security(code STRING, PRIMARY KEY(code))",
    "CREATE REL TABLE IF NOT EXISTS HOLDS("
    "FROM Fund TO Security, weight DOUBLE, as_of STRING, source STRING, "
    "valid_from STRING, valid_to STRING)",
)

def _window_read(returned: str) -> str:
    """Cypher for the HOLDS disclosure live at ``$as_of`` per holder, anchored on ``$code``.

    HOLDS always points Fund→Security; ``returned`` (``"Fund"`` or ``"Security"``) is the side we
    list, the other side is pinned by ``$code``. An edge is *live* at ``$as_of`` when
    ``valid_from <= $as_of < valid_to`` (the half-open window; ``valid_to`` is exclusive, so a
    closed position drops out — survivorship-aware). Per holder we take the latest
    ``max(valid_from)`` (``valid_from`` is a zero-padded ISO date, so lexical max is the
    chronological latest), then the max weight in that window, then the max source at that weight —
    so the returned ``(weight, source)`` is always one *real* disclosure (never a per-column mix).
    An earlier query date never sees a later disclosure.
    """

    def hop(edge: str, *, first: bool) -> str:
        node = f"(n:{returned})" if first else "(n)"  # ``n`` keeps its label only when first bound
        anchor = "(:Security {code: $code})" if returned == "Fund" else "(:Fund {code: $code})"
        left, right = (node, anchor) if returned == "Fund" else (anchor, node)
        return f"{left}-[{edge}:HOLDS]->{right}"

    def live(var: str) -> str:
        return f"{var}.valid_from <= $as_of AND $as_of < {var}.valid_to"

    return (
        f"MATCH {hop('h', first=True)} WHERE {live('h')} "
        "WITH n, max(h.valid_from) AS vf "
        f"MATCH {hop('e', first=False)} WHERE {live('e')} AND e.valid_from = vf "
        "WITH n, vf, max(e.weight) AS weight "
        f"MATCH {hop('g', first=False)} "
        f"WHERE {live('g')} AND g.valid_from = vf AND g.weight = weight "
        "RETURN n.code, weight, max(g.as_of), vf, max(g.valid_to), max(g.source) ORDER BY n.code"
    )


_FUNDS_HOLDING = _window_read("Fund")
_SECURITIES_OF = _window_read("Security")


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
        before = self.count()
        # MERGE the (idempotent) nodes, then MERGE one edge per disclosure identity — the endpoints
        # plus (as_of, source, valid_from). ON CREATE SET writes the payload only on first insert,
        # so re-ingesting the same disclosure is a no-op while a genuinely new disclosure (a new
        # as_of) is a new row. Node and edge MERGEs are split so one (fund, security) pair can carry
        # several disclosures.
        self._con.execute("UNWIND $rows AS r MERGE (:Fund {code: r.fund})", {"rows": rows})
        self._con.execute("UNWIND $rows AS r MERGE (:Security {code: r.security})", {"rows": rows})
        self._con.execute(
            "UNWIND $rows AS r MATCH (f:Fund {code: r.fund}), (s:Security {code: r.security}) "
            "MERGE (f)-[h:HOLDS {as_of: r.as_of, source: r.source, valid_from: r.valid_from}]->(s) "
            "ON CREATE SET h.weight = r.weight, h.valid_to = r.valid_to",
            {"rows": rows},
        )
        return self.count() - before

    def funds_holding(self, security: str, as_of: str) -> list[Edge]:
        rows = self._read(_FUNDS_HOLDING, {"code": security, "as_of": as_of})
        return [
            Edge(fund=fund, security=security, weight=w, as_of=ao, source=src,
                 valid_from=vf, valid_to=vt)
            for fund, w, ao, vf, vt, src in rows
        ]

    def securities_of(self, fund: str, as_of: str) -> list[Edge]:
        rows = self._read(_SECURITIES_OF, {"code": fund, "as_of": as_of})
        return [
            Edge(fund=fund, security=security, weight=w, as_of=ao, source=src,
                 valid_from=vf, valid_to=vt)
            for security, w, ao, vf, vt, src in rows
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


def _window_edges(readings: Iterable[Reading], *, fund_key: str, source: str) -> list[Edge]:
    """Holdings readings → HOLDS edges, each window closed at the fund's *next* snapshot.

    A holding stays in force until the fund discloses again: ``valid_to`` is the fund's next
    snapshot date after this row's ``as_of`` (``OPEN_END`` if this is its latest). So a name
    re-disclosed next quarter reopens a fresh window, while a name that drops out simply expires —
    survivorship-aware, with no name carried forward as if still held. Snapshot dates are taken from
    ``readings`` itself, so each source's calendar is its own (an N-PORT date never closes a 13F
    window, and vice versa).
    """

    rows = list(readings)
    snapshots: dict[str, set[str]] = {}
    for r in rows:
        snapshots.setdefault(str(r.payload[fund_key]), set()).add(r.as_of)
    edges: list[Edge] = []
    for r in rows:
        fund = str(r.payload[fund_key])
        later = [d for d in snapshots[fund] if d > r.as_of]
        edges.append(
            Edge(
                fund=fund,
                security=str(r.payload["holding"]),
                weight=float(r.payload["weight"]),
                as_of=r.as_of,
                source=source,
                valid_to=min(later) if later else OPEN_END,
            )
        )
    return edges


def build_graph_from_store(graph: GraphStore, store: PointInTimeStore) -> int:
    """Materialise the HOLDS graph from the weighted holdings readings (no LLM).

    Reads the *full* append-only history (every quarter's disclosure) so the graph keeps its own
    point-in-time read at query time — an earlier snapshot never sees a later disclosure, and each
    holding's window closes at the fund's next disclosure (see :func:`_window_edges`). Both feeds of
    weighted fund/ETF holdings become edges: CN ``fund_holdings`` and US N-PORT ``edgar`` rows
    (which carry a ``weight``); 13F manager positions carry ``shares`` not a weight and are skipped.
    Returns the number of edges added.
    """

    edges = _window_edges(store.history("fund_holdings"), fund_key="fund", source="fund_holdings")
    edges += _window_edges(
        # N-PORT fund/ETF holdings carry a weight; 13F (shares-only) is not a graph edge.
        (r for r in store.history("edgar") if "weight" in r.payload),
        fund_key="filer",
        source="edgar",
    )
    return graph.add_edges(edges)
