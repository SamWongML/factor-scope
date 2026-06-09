"""The nightly pipeline — orchestrates the layers into one ``dashboard.json``.

This module wires the point-in-time store between ingestion and the artifact: ``ingest`` fills the
store and ``build_dashboard`` reads it (point-in-time, as of the run date) to produce the three
lists with real ``evidence[]`` and a per-item ``gain`` (cost basis vs current NAV). The snapshot
boundary is one-way — research/ingest *fetches* and writes dated Readings; ``run`` reasons over the
frozen snapshot deterministically and never fetches. So a fixtures ``run`` materialises its offline
snapshot to stay standalone, while a *live* ``run`` against an empty store refuses (``ingest``
first) rather than reaching for the network; the artifact records the snapshot id it read. Each item
is then enriched in place — states and gate, look-through connections, the scorecard, a calibrated
lean, and the emerging list — without changing this entrypoint's contract.
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
from factor_scope.discovery import (
    build_stream_docs,
    discover_themes,
    get_assessor,
    get_topic_model,
)
from factor_scope.emerging import (
    Candidate,
    FundScore,
    Shortlist,
    Theme,
    infer_links,
    run_funnel,
)
from factor_scope.factors import FactorContext, compute_gate, compute_states
from factor_scope.graph import (
    GraphStore,
    Holding,
    LadybugGraphStore,
    build_connections,
    build_graph_from_store,
)
from factor_scope.graph.lookthrough import look_through
from factor_scope.ingest import textstream
from factor_scope.ingest.base import fetched_at_for
from factor_scope.markets import Market, get_market
from factor_scope.schedule import DigestFailure, RunRecord, append_run_log, summarize_run
from factor_scope.scoring import Call, build_scorecard, log_call, read_calls, score_calls
from factor_scope.store import DuckDBStore, PointInTimeStore, Reading


class SnapshotError(RuntimeError):
    """``run`` was asked to reason over a snapshot that does not exist yet.

    The snapshot boundary is one-way: research/ingest fetches and writes Readings; ``run`` only
    reads a frozen snapshot. A live source against an empty store cannot be reasoned over without
    fetching, so ``run`` refuses rather than crossing back over the boundary.
    """


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


def _open_graph(config: Config) -> LadybugGraphStore:
    return LadybugGraphStore(":memory:" if config.graph_path is None else config.graph_path)


def _resolve_market(config: Config, market: Market | None) -> Market:
    """The injected market (tests) or the one ``config`` selects by name."""

    return market if market is not None else get_market(config.market)


def ingest(config: Config, *, market: Market | None = None) -> int:
    """Fill the store + connection graph from the source. Returns the number of rows appended."""

    as_of = _resolve_as_of(config)
    mkt = _resolve_market(config, market)
    store = _open_store(config)
    graph = _open_graph(config)
    try:
        n = store.append(mkt.gather(config, as_of=as_of))
        build_graph_from_store(graph, store)
        n += _materialise_mapping(store, graph, as_of)
        return n
    finally:
        store.close()
        graph.close()


def discover(config: Config) -> int:
    """Discover candidate themes from the text stream → append ``themes`` Readings. Returns count.

    The separate, user/cron-triggered research service (not the nightly): it *fetches* the rolling
    corpus and *writes* dated Readings on the research side of the snapshot boundary, leaving the
    nightly's deterministic reasoning untouched — the next ``ingest`` maps the discovered themes to
    funds and ``run`` surfaces them. Offline reads the bundled corpus + the deterministic fakes;
    online pulls the feed and runs BERTopic + the configured LLM (the opt-in ``discovery`` extra).
    """

    as_of = _resolve_as_of(config)
    fetched_at = fetched_at_for(as_of)
    store = _open_store(config)
    try:
        if config.source == "fixtures":
            corpus = textstream.load_fixture(
                config.fixtures_dir / textstream.FIXTURE, fetched_at=fetched_at
            )
        else:  # pragma: no cover - opt-in live path
            if config.textstream_feed_url is None:
                raise SnapshotError(
                    "no text corpus to discover from: set a textstream feed "
                    "(Config.textstream_feed_url) or use --offline for the bundled corpus"
                )
            corpus = textstream.fetch_live(config.textstream_feed_url, fetched_at=fetched_at)
        store.append(corpus)
        docs = build_stream_docs(store.read_as_of(textstream.SERIES, as_of))
        themes = discover_themes(
            docs,
            get_topic_model(config),
            get_assessor(config),
            as_of=as_of,
            fetched_at=fetched_at,
        )
        return store.append(themes)
    finally:
        store.close()


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
            if "divergence" in price.payload:  # cross-source reconciliation flag — show, don't hide
                peer = float(price.payload["divergence"])
                source = str(price.payload.get("source", "primary"))
                evidence.append(
                    Evidence(
                        src="prices:unreconciled",
                        as_of=price.as_of,
                        one_line=(
                            f"NAV unreconciled across sources: {source} {nav:g} "
                            f"vs peer {peer:g} — needs review"
                        ),
                    )
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
    """Score the prior calls knowable tonight and attach the rolling mirror to each item.

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
    pairs: list[tuple[str, DashboardItem]],
    store: PointInTimeStore,
    as_of: str,
    provider_name: str,
    *,
    digest_failures: list[DigestFailure] | None = None,
) -> None:
    """Digest each item into a calibrated lean, then log it as a falsifiable call.

    The bull/bear→synthesis runs on the selected provider (the deterministic ``fake`` by default);
    the orchestrator enforces the gate, abstain, and scorecard guardrails. A seat that *raises*
    degrades that item to abstain (``digest_item``); the error is appended to ``digest_failures``
    (when a sink is given) for the ops run log. Each emitted lean is appended to the point-in-time
    store as a :class:`~factor_scope.scoring.Call` — stamped with tonight's date and immutable — so
    next run's self-scoring loop scores *this* real call.
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
        if result.error is not None and digest_failures is not None:
            digest_failures.append(DigestFailure(code=code, error=result.error))
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
        constituents=tuple(p.get("constituents") or ()),
    )


def _candidate_from_reading(reading: Reading) -> Candidate:
    p = reading.payload
    return Candidate(
        theme=str(p["theme"]),
        code=str(p["code"]),
        name=str(p["name"]),
        methodology=float(p["methodology"]),
        fee=float(p["fee"]),
        aum=float(p["aum"]),
        tracking_error=float(p["tracking_error"]),
        top10_weight=float(p["top10_weight"]),
        crowding=float(p["crowding"]),
        as_of=reading.as_of,
    )


def _materialise_mapping(store: PointInTimeStore, graph: GraphStore, as_of: str) -> int:
    """Infer the theme→fund mapping from the graph + prices; cache it as dated ``theme_map`` rows.

    The Stage-B candidate set is *derived*, not hand-tagged: each theme's reference constituents
    seed a holdings-overlap (重合度) + return-correlation (涨跌幅相关性) ranking of the fund
    universe (:func:`~factor_scope.emerging.infer_links`). For each inferred link the per-fund
    scorecard inputs are joined point-in-time from the universe feeds — name/fee/tracking/top-10
    from ``fund_universe``, AUM from ``etf_scale``, the theme's own crowding from ``themes`` — and
    ``methodology`` is the *measured* pure-play (the mapping score). The result is frozen as
    ``theme_map`` Readings keyed by ``theme:code`` (so a fund can map to several themes) with a
    deterministic ``fetched_at``, so a later disclosure never rewrites a past mapping. Returns the
    number of rows appended.
    """

    themes = store.read_as_of("themes", as_of)
    universe = {
        r.key: r for r in store.read_as_of("fund_universe", as_of) if r.payload.get("valid")
    }
    aum = {r.key: float(r.payload["aum"]) for r in store.read_as_of("etf_scale", as_of)}
    constituents = {r.key: list(r.payload.get("constituents") or ()) for r in themes}
    crowding = {r.key: float(r.payload["crowding"]) for r in themes}
    codes = sorted(universe.keys() & aum.keys())  # funds with both a full scorecard and a size read
    fetched_at = fetched_at_for(as_of)
    rows = [
        Reading(
            series="theme_map",
            key=f"{link.theme}:{link.code}",
            as_of=as_of,
            fetched_at=fetched_at,
            payload={
                "theme": link.theme,
                "code": link.code,
                "name": str(universe[link.code].payload["name"]),
                "methodology": link.score,  # measured pure-play (重合度 confirmed by correlation)
                "fee": float(universe[link.code].payload["fee"]),
                "aum": aum[link.code],
                "tracking_error": float(universe[link.code].payload["tracking_error"]),
                "top10_weight": float(universe[link.code].payload["top10_weight"]),
                "crowding": crowding[link.theme],
                "overlap": link.overlap,
                "correlation": link.correlation,
            },
        )
        for link in infer_links(constituents, codes, graph, store, as_of)
    ]
    return store.append(rows)


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
            f"crowding {c.crowding:.0%} · overlap-with-core {score.overlap:.1%}"
        ),
    )


def _emerging_connections(
    score: FundScore, graph: GraphStore, as_of: str, book: list[Holding]
) -> list[Connection]:
    """Surface the candidate's overlap with my core as connections (the leveraged repeat)."""

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
    """Run the two-stage funnel → the ``emerging`` list (top-3 funds per cleared theme).

    Stage A qualifies each industry; Stage B screens a cleared theme's candidate funds on the fixed
    scorecard (overlap-with-core via the look-through) to a ranked top 3. Each surviving fund
    becomes an emerging item carrying its factor states/gate (where price history exists), the
    Stage-A/Stage-B one-page comparison as evidence, and its overlap as connections. The digest
    then leans over the shortlist (in ``_attach_leans``) and promotes at most one.
    """

    if store.count("themes") == 0:
        return []
    themes = [_theme_from_reading(r) for r in store.read_as_of("themes", as_of)]
    candidates = [_candidate_from_reading(r) for r in store.read_as_of("theme_map", as_of)]
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


def build_dashboard(
    config: Config,
    *,
    digest_failures: list[DigestFailure] | None = None,
    market: Market | None = None,
) -> Dashboard:
    """Build the morning artifact for one run from the point-in-time store.

    Determinism: the as-of date is the override or the fixture stamp (never the wall clock), and a
    fixtures ingest is stamped deterministically, so a fixtures run reproduces byte-for-byte.

    ``digest_failures`` is an optional sink the nightly job passes to collect any items whose seat
    call raised (each degraded to abstain) for the ops run log; it never affects the artifact.
    ``market`` overrides the config-selected adapter (used to drive the pipeline from fake sources).
    """

    as_of = _resolve_as_of(config)
    store = _open_store(config)
    graph = _open_graph(config)
    try:
        if store.count("positions") == 0:
            if market is None and config.source == "live":
                # The snapshot boundary: `run` reasons over a frozen snapshot, it never fetches.
                # A live source must be pulled by `ingest` first; `run` then reads what it wrote.
                raise SnapshotError(
                    "no snapshot to reason over: run `factor-scope ingest` first, "
                    "then `run --store-path …` reads the frozen snapshot it wrote"
                )
            # Offline snapshot (fixtures, or an injected test market) → materialise it so `run`
            # works standalone. Reading committed files is not fetching; the artifact stays
            # byte-for-byte deterministic.
            store.append(_resolve_market(config, market).gather(config, as_of=as_of))
        if graph.count() == 0:
            # Empty graph → build it from the (durable or just-ingested) holdings readings.
            build_graph_from_store(graph, store)
        if store.count("theme_map") == 0:
            # Derive + freeze the theme→fund mapping if `ingest` did not already cache it, so the
            # offline `run` reasons over the same data-derived candidates as the durable nightly.
            _materialise_mapping(store, graph, as_of)
        # Fingerprint the frozen snapshot the run reasons over — the read data, not the calls this
        # run is about to log (those are derived output, so excluding them keeps a re-run stable).
        snapshot_id = store.snapshot_id(as_of, exclude=("calls",))
        book = _build_book(store, as_of)
        core_pairs = _build_items(store, as_of)
        _attach_connections(core_pairs, graph, book, as_of)
        # The emerging list is the funnel's output, not a hand-placed position; it owns
        # its own overlap-with-core connections, so it is built after the core look-through.
        emerging_pairs = _build_emerging(store, graph, as_of, book)
        pairs = core_pairs + emerging_pairs
        _attach_scorecard(pairs, store, as_of)
        _attach_leans(pairs, store, as_of, config.provider, digest_failures=digest_failures)
        items = [item for _, item in pairs]
    finally:
        store.close()
        graph.close()

    return Dashboard(
        as_of=as_of, generated_at=f"{as_of}T22:00:00Z", snapshot_id=snapshot_id, items=items
    )


def run(
    config: Config,
    *,
    digest_failures: list[DigestFailure] | None = None,
    market: Market | None = None,
) -> Dashboard:
    """Build the dashboard and persist it to ``config.output_path``."""

    dash = build_dashboard(config, digest_failures=digest_failures, market=market)
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

    Positions are stamped with the run's ``as_of``, so this is per-night: re-running the same
    night is a no-op (keeps the artifact byte-for-byte and never double-counts calls in the
    audit-trail scorer), while a new night still ingests fresh data.
    """

    store = _open_store(config)
    try:
        return any(p.as_of == as_of for p in store.history("positions"))
    finally:
        store.close()


def nightly(
    config: Config,
    *,
    clock: Callable[[], str] = _utc_now_iso,
    market: Market | None = None,
) -> tuple[Dashboard, RunRecord]:
    """The one-shot nightly job: ingest → compute → digest → artifact → run log.

    Runs the full pipeline against a *durable* store so the leans it emits persist as falsifiable
    calls — tomorrow's self-scoring loop scores them. Appends one structured :class:`RunRecord` to
    the append-only ops log. The artifact stays byte-for-byte deterministic; the run log carries the
    wall-clock timing (operations telemetry, not the decision artifact).
    """

    as_of = _resolve_as_of(config)
    started_at = clock()
    if not _night_already_ingested(config, as_of):
        # append the night's readings into the durable store + materialise the graph
        ingest(config, market=market)
    digest_failures: list[DigestFailure] = []
    # build + write; logs each lean as a call
    dash = run(config, digest_failures=digest_failures, market=market)
    ended_at = clock()
    record = summarize_run(
        dash,
        started_at=started_at,
        ended_at=ended_at,
        provider=config.provider,
        n_calls_logged=_count_calls_logged(config, as_of),
        output_path=str(config.output_path),
        digest_failures=tuple(digest_failures),
    )
    append_run_log(config.log_path, record)
    return dash, record
