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
from collections.abc import Callable
from datetime import UTC, datetime

from factor_scope.config import Config
from factor_scope.contract import (
    Connection,
    Dashboard,
    DashboardItem,
    Evidence,
    GateState,
    Lean,
    LeanAction,
    ListName,
)
from factor_scope.digest import DEFAULT_HORIZON_D, DigestInput, digest_item, get_provider
from factor_scope.emerging import (
    Candidate,
    FundScore,
    Shortlist,
    Theme,
    run_funnel,
)
from factor_scope.factors import FactorContext, compute_gate, compute_states
from factor_scope.graph import (
    DuckDBGraphStore,
    GraphStore,
    Holding,
    build_connections,
    build_graph_from_store,
)
from factor_scope.graph.lookthrough import look_through
from factor_scope.ingest import gather_fixture_readings, gather_live_readings
from factor_scope.schedule import RunRecord, append_run_log, summarize_run
from factor_scope.scoring import Call, build_scorecard, log_call, read_calls, score_calls
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

    One descriptive scorecard is built from the resolved calls inside the rolling window — the
    book-wide calibration mirror tomorrow's digest reads — and shared onto every item. It is
    read-only: it never touches an item's states or gate (see ``test_guardrails``). With no calls
    logged, nothing is attached.
    """

    if store.count("calls") == 0:
        return
    scorecard = build_scorecard(score_calls(store, as_of), as_of)
    for _, item in pairs:
        item.scorecard = scorecard


def _prior_action(store: PointInTimeStore, code: str, as_of: str) -> LeanAction | None:
    """The most recent prior lean on this code (latest as_of, then call_id) — for evolution."""

    prior = [c for c in read_calls(store, as_of) if c.code == code and c.as_of < as_of]
    if not prior:
        return None
    return max(prior, key=lambda c: (c.as_of, c.call_id)).action


def _attach_leans(
    pairs: list[tuple[str, DashboardItem]], store: PointInTimeStore, as_of: str, provider_name: str
) -> None:
    """Digest each item into a calibrated lean, then log it as a falsifiable call (spec §08/§06).

    The bull/bear→synthesis runs on the selected provider (the deterministic ``fake`` by default);
    the orchestrator enforces the gate, abstain, and scorecard guardrails. Each emitted lean is
    appended to the point-in-time store as a :class:`~factor_scope.scoring.Call` — stamped with
    tonight's date and immutable — so next run's self-scoring loop scores *this* real call.
    """

    provider = get_provider(provider_name)
    # Idempotent on a durable store: never log a second call for a code already called tonight, so
    # re-running the same night can't double-count in next run's score (the store is append-only).
    logged_tonight = {c.call_id for c in read_calls(store, as_of) if c.as_of == as_of}
    for code, item in pairs:
        brief = DigestInput(
            code=code,
            name=item.item,
            list_name=item.list_name,
            states=tuple(item.states),
            gate=item.gate,
            connections=tuple(item.connections),
            connections_flag=item.connections_flag,
            gain=item.gain,
            scorecard=item.scorecard,
            prior_action=_prior_action(store, code, as_of),
            evidence=tuple(item.evidence),
            as_of=as_of,
        )
        result = digest_item(provider, brief)
        item.lean = Lean(action=result.action, confidence=result.confidence, text=result.text)
        item.evolution = result.evolution
        item.flip_trigger = result.flip_trigger
        item.invalidation = result.invalidation
        call_id = f"{code}:{as_of}"
        if call_id in logged_tonight:
            continue
        log_call(
            store,
            Call(
                call_id=call_id,
                code=code,
                as_of=as_of,
                action=result.action,
                confidence=result.confidence,
                horizon_d=DEFAULT_HORIZON_D,
                state_pattern=result.state_pattern,
                invalidation=result.invalidation,
            ),
            fetched_at=as_of,
        )
        logged_tonight.add(call_id)


def _theme_from_reading(reading: Reading) -> Theme:
    p = reading.payload
    return Theme(
        name=reading.key,
        acceleration=float(p["acceleration"]),
        base_level=float(p["base_level"]),
        breadth=int(p["breadth"]),
        crowding=float(p["crowding"]),
        broad_adoption=bool(p["broad_adoption"]),
        path_to_profit=bool(p["path_to_profit"]),
        fad_resistant=bool(p["fad_resistant"]),
        lead_chain=bool(p["lead_chain"]),
        wrapper_exists=bool(p["wrapper_exists"]),
        as_of=reading.as_of,
    )


def _candidate_from_reading(reading: Reading) -> Candidate:
    p = reading.payload
    return Candidate(
        theme=str(p["theme"]),
        code=reading.key,
        name=str(p["name"]),
        methodology=float(p["methodology"]),
        fee=float(p["fee"]),
        aum=float(p["aum"]),
        tracking_error=float(p["tracking_error"]),
        top10_weight=float(p["top10_weight"]),
        as_of=reading.as_of,
    )


def _stage_a_evidence(shortlist: Shortlist) -> Evidence:
    return Evidence(
        src="emerging:stage_a",
        as_of=shortlist.as_of,
        one_line=f"theme {shortlist.theme} cleared Stage A — "
        + "; ".join(shortlist.stage_a.reasons),
    )


def _stage_b_evidence(score: FundScore, rank: int, n_candidates: int) -> Evidence:
    c = score.candidate
    return Evidence(
        src="emerging:stage_b",
        as_of=c.as_of,
        one_line=(
            f"rank #{rank}/{n_candidates} · score {score.total:.2f} · {c.theme} · "
            f"methodology {c.methodology:.2f} · fee {c.fee:.2%} · AUM {c.aum:g}亿 · "
            f"tracking {c.tracking_error:.1%} · top10 {c.top10_weight:.0%} · "
            f"overlap-with-core {score.overlap:.1%}"
        ),
    )


def _emerging_connections(
    score: FundScore, graph: GraphStore, as_of: str, book: list[Holding]
) -> list[Connection]:
    """Surface the candidate's overlap with my core as §05 connections (the leveraged repeat)."""

    names = {h.code: h.name for h in book}
    connections: list[Connection] = []
    for security in score.overlap_names:
        lt = look_through(graph, security, as_of, book)
        connections.append(
            Connection(
                shared=security,
                also_in=[names[code] for code in lt.funds],
                lookthrough_wt=round(lt.lookthrough_wt, 6),
            )
        )
    return connections


def _build_emerging(
    store: PointInTimeStore, graph: GraphStore, as_of: str, book: list[Holding]
) -> list[tuple[str, DashboardItem]]:
    """Run the two-stage funnel → the ``emerging`` list (top-3 funds per cleared theme, spec §07).

    Stage A qualifies each industry; Stage B screens a cleared theme's candidate funds on the fixed
    scorecard (overlap-with-core via the §05 look-through) to a ranked top 3. Each surviving fund
    becomes an emerging item carrying its factor states/gate (where price history exists), the
    Stage-A/Stage-B one-page comparison as evidence, and its overlap as connections. The digest
    then leans over the shortlist (in ``_attach_leans``) and promotes at most one.
    """

    if store.count("themes") == 0:
        return []
    themes = [_theme_from_reading(r) for r in store.read_as_of("themes", as_of)]
    candidates = [_candidate_from_reading(r) for r in store.read_as_of("theme_funds", as_of)]
    pairs: list[tuple[str, DashboardItem]] = []
    for shortlist in run_funnel(themes, candidates, graph, as_of, book):
        for rank, score in enumerate(shortlist.funds, start=1):
            code = score.candidate.code
            ctx = FactorContext(code=code, as_of=as_of, store=store)
            connections = _emerging_connections(score, graph, as_of, book)
            item = DashboardItem(
                item=score.candidate.name,
                list=ListName.EMERGING,
                states=compute_states(ctx),
                gate=compute_gate(ctx),
                connections=connections,
                connections_flag=bool(connections),
                evidence=[
                    _stage_a_evidence(shortlist),
                    _stage_b_evidence(score, rank, shortlist.n_candidates),
                ],
            )
            pairs.append((code, item))
    return pairs


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
        book = _build_book(store, as_of)
        core_pairs = _build_items(store, as_of)
        _attach_connections(core_pairs, graph, book, as_of)
        # The emerging list is the funnel's output (spec §07), not a hand-placed position; it owns
        # its own overlap-with-core connections, so it is built after the core look-through.
        emerging_pairs = _build_emerging(store, graph, as_of, book)
        pairs = core_pairs + emerging_pairs
        _attach_scorecard(pairs, store, as_of)
        _attach_leans(pairs, store, as_of, config.provider)
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


def _utc_now_iso() -> str:
    """Wall-clock stamp for the ops run log (never the artifact — that stays clock-free)."""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_calls_logged(config: Config, as_of: str) -> int:
    """How many leans were logged for ``as_of`` in the durable store (tomorrow's scoring fuel)."""

    store = _open_store(config)
    try:
        return sum(1 for c in read_calls(store, as_of) if c.as_of == as_of)
    finally:
        store.close()


def _night_already_ingested(config: Config, as_of: str) -> bool:
    """True once tonight's positions are in the durable store — so re-runs don't re-ingest.

    Positions are stamped with the run's ``as_of`` (D7), so this is per-night: re-running the same
    night is a no-op (keeps the artifact byte-for-byte and never double-counts calls in the
    audit-trail scorer), while a new night still ingests fresh data.
    """

    store = _open_store(config)
    try:
        return any(p.as_of == as_of for p in store.history("positions"))
    finally:
        store.close()


def nightly(
    config: Config, *, clock: Callable[[], str] = _utc_now_iso
) -> tuple[Dashboard, RunRecord]:
    """The one-shot nightly job (spec §11): ingest → compute → digest → artifact → run log.

    Runs the full pipeline against a *durable* store so the leans it emits persist as falsifiable
    calls — tomorrow's self-scoring loop scores them. Appends one structured :class:`RunRecord` to
    the append-only ops log. The artifact stays byte-for-byte deterministic; the run log carries the
    wall-clock timing (operations telemetry, not the decision artifact).
    """

    as_of = _resolve_as_of(config)
    started_at = clock()
    if not _night_already_ingested(config, as_of):
        ingest(config)  # append the night's readings into the durable store + materialise the graph
    dash = run(config)  # build + write dashboard.json; logs each lean as a call into the store
    ended_at = clock()
    record = summarize_run(
        dash,
        started_at=started_at,
        ended_at=ended_at,
        provider=config.provider,
        n_calls_logged=_count_calls_logged(config, as_of),
        output_path=str(config.output_path),
    )
    append_run_log(config.log_path, record)
    return dash, record
