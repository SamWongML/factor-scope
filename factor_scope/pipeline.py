"""The nightly pipeline — orchestrates the layers into one ``dashboard.json``.

Phase 1 wires the point-in-time store between ingestion and the artifact: ``ingest`` fills the
store and ``build_dashboard`` reads it (point-in-time, as of the run date) to produce the three
lists with real ``evidence[]`` and a per-item ``gain`` (cost basis vs current NAV). So the
entrypoint keeps working standalone, a fixtures ``run`` against an empty store auto-ingests first.
Later phases enrich each item in place — states + gate (P2), connections (P3), scorecard (P4), the
lean (P5), the emerging list (P6) — without changing this entrypoint's contract.
"""

from __future__ import annotations

import json

from factor_scope.config import Config
from factor_scope.contract import (
    Dashboard,
    DashboardItem,
    Evidence,
    GateState,
    ListName,
)
from factor_scope.factors import FactorContext, compute_gate, compute_states
from factor_scope.graph import (
    DuckDBGraphStore,
    GraphStore,
    Holding,
    build_connections,
    build_graph_from_store,
)
from factor_scope.ingest import gather_fixture_readings, gather_live_readings
from factor_scope.scoring import build_scorecard, score_calls
from factor_scope.store import DuckDBStore, PointInTimeStore, Reading


def _resolve_as_of(config: Config) -> str:
    """The point-in-time date the engine reasons on: the CLI override, else the fixture stamp."""

    if config.as_of:
        return config.as_of
    manifest = config.fixtures_dir / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError(
            f"no as_of given and fixture manifest not found: {manifest}. "
            "Run from the repo root, pass --as-of, or use --fixtures-dir."
        )
    data: dict[str, str] = json.loads(manifest.read_text(encoding="utf-8"))
    return data["as_of"]


def _open_store(config: Config) -> DuckDBStore:
    return DuckDBStore(":memory:" if config.store_path is None else config.store_path)


def _open_graph(config: Config) -> DuckDBGraphStore:
    return DuckDBGraphStore(":memory:" if config.graph_path is None else config.graph_path)


def _gather(config: Config, as_of: str) -> list[Reading]:
    if config.source == "fixtures":
        return gather_fixture_readings(config, as_of=as_of)
    return gather_live_readings(config, as_of=as_of)


def ingest(config: Config) -> int:
    """Fill the store + connection graph from the source. Returns the number of rows appended."""

    as_of = _resolve_as_of(config)
    store = _open_store(config)
    graph = _open_graph(config)
    try:
        n = store.append(_gather(config, as_of))
        build_graph_from_store(graph, store)
        return n
    finally:
        store.close()
        graph.close()


def _build_items(store: PointInTimeStore, as_of: str) -> list[tuple[str, DashboardItem]]:
    """Project the store into ``(code, item)`` pairs (positions + priced evidence + states/gate)."""

    prices = {r.key: r for r in store.read_as_of("prices", as_of)}
    items: list[tuple[str, DashboardItem]] = []
    for pos in store.read_as_of("positions", as_of):
        cost_basis = float(pos.payload["cost_basis"])
        gain: float | None = None
        evidence: list[Evidence] = []
        price = prices.get(pos.key)
        if price is not None:
            nav = float(price.payload["nav"])
            if cost_basis > 0:
                gain = (nav - cost_basis) / cost_basis
            gain_text = f"{gain:+.1%}" if gain is not None else "n/a"
            one_line = (
                f"NAV {nav:g} (as of {price.as_of}); gain {gain_text} vs cost {cost_basis:g}"
            )
            evidence.append(
                Evidence(src="akshare:fund_etf_hist", as_of=price.as_of, one_line=one_line)
            )
        ctx = FactorContext(code=pos.key, as_of=as_of, store=store)
        item = DashboardItem(
            item=str(pos.payload["name"]),
            list=ListName(pos.payload["list"]),
            gain=gain,
            states=compute_states(ctx),
            gate=compute_gate(ctx),
            evidence=evidence,
        )
        items.append((pos.key, item))
    return items


def _build_book(store: PointInTimeStore, as_of: str) -> list[Holding]:
    """My funds + my portfolio weight in each (by market value: shares × point-in-time NAV).

    A not-yet-held watch name has zero weight — it can still surface in an overlap, but adds no
    look-through exposure. If nothing is held (every value zero) all weights are zero.
    """

    prices = {r.key: r for r in store.read_as_of("prices", as_of)}
    rows: list[tuple[str, str, float]] = []  # (code, name, market value)
    for pos in store.read_as_of("positions", as_of):
        shares = float(pos.payload.get("shares", 0) or 0)
        price = prices.get(pos.key)
        nav = float(price.payload["nav"]) if price is not None else 0.0
        rows.append((pos.key, str(pos.payload["name"]), shares * nav))
    total = sum(value for _, _, value in rows)
    return [
        Holding(code=code, name=name, weight=(value / total if total > 0 else 0.0))
        for code, name, value in rows
    ]


def _is_down(item: DashboardItem) -> bool:
    """A fund is at downside risk if the trend gate is capped or it reads reversal-DOWN risk."""

    if item.gate is GateState.CAPPED:
        return True
    return any(s.valid and "reversal-DOWN" in s.direction for s in item.states)


def _attach_connections(
    pairs: list[tuple[str, DashboardItem]],
    graph: GraphStore,
    book: list[Holding],
    as_of: str,
) -> None:
    """Fill each item's ``connections[]`` + ``connections_flag`` via the exact look-through.

    A held security is flagged falling (``↓``) when *any* of my funds holding it is at downside
    risk — so the shared name inherits the warning across the whole book.
    """

    down: set[str] = set()
    for code, item in pairs:
        if _is_down(item):
            down.update(e.security for e in graph.securities_of(code, as_of))
    for code, item in pairs:
        connections, flag = build_connections(graph, code, as_of, book, down)
        item.connections = connections
        item.connections_flag = flag


def _attach_scorecard(
    pairs: list[tuple[str, DashboardItem]], store: PointInTimeStore, as_of: str
) -> None:
    """Score the prior calls knowable tonight and attach the rolling mirror to each item (spec §06).

    One descriptive scorecard is built from *all* resolved calls — the book-wide calibration mirror
    tomorrow's digest reads — and shared onto every item. It is read-only: it never touches an
    item's states or gate (see ``test_guardrails``). With no calls logged, nothing is attached.
    """

    if store.count("calls") == 0:
        return
    scorecard = build_scorecard(score_calls(store, as_of))
    for _, item in pairs:
        item.scorecard = scorecard


def build_dashboard(config: Config) -> Dashboard:
    """Build the morning artifact for one run from the point-in-time store.

    Determinism: the as-of date is the override or the fixture stamp (never the wall clock), and a
    fixtures ingest is stamped deterministically, so a fixtures run reproduces byte-for-byte.
    """

    as_of = _resolve_as_of(config)
    store = _open_store(config)
    graph = _open_graph(config)
    try:
        if store.count("positions") == 0:
            # Empty store → auto-ingest so `run` works without a separate `ingest` step.
            store.append(_gather(config, as_of))
        if graph.count() == 0:
            # Empty graph → build it from the (durable or just-ingested) holdings readings.
            build_graph_from_store(graph, store)
        pairs = _build_items(store, as_of)
        _attach_connections(pairs, graph, _build_book(store, as_of), as_of)
        _attach_scorecard(pairs, store, as_of)
        items = [item for _, item in pairs]
    finally:
        store.close()
        graph.close()

    return Dashboard(as_of=as_of, generated_at=f"{as_of}T22:00:00Z", items=items)


def run(config: Config) -> Dashboard:
    """Build the dashboard and persist it to ``config.output_path``."""

    dash = build_dashboard(config)
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(dash.model_dump_json(indent=2), encoding="utf-8")
    return dash
