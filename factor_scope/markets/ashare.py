"""A-share / funds-and-ETF market — the first concrete :class:`~factor_scope.markets.base.Market`.

The book (the local ``positions.csv``) plus the full fund universe behind it
(:class:`AShareUniverse`), CN NAVs reconciled across AkShare/Baostock/Mootdx
(:class:`ASharePrices`), and the emerging-theme candidates (:class:`AShareThemes`). On top of those
three seams the market adds the FRED macro dial, the end-demand dial, the US (EDGAR) lead-chain, and
the engine's own prior leans (seed data for the offline self-scoring loop).

The universe loop and the price reconciliation run **one** code path: a
:class:`~factor_scope.ingest.feed.Feed` supplies the raw reads — the live network adapters online,
the committed cassettes offline (see ``factor_scope.ingest.feed``) — so the expensive, bug-prone
live behaviour (the universe-wide loop, the incremental watermark, the multi-source reconciliation,
delisting detection) is exercised in both modes. The seed reads that have no live nightly feed —
emerging themes (discovery is a separate service), the FRED/EDGAR dials (each pulls only the latest
observation live, accruing history over nights), and the prior-call self-scoring seed — load the
bundled fixtures offline.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from factor_scope.config import Config
from factor_scope.credentials import resolve_credential
from factor_scope.ingest import (
    IngestDeadline,
    _bounded,
    _check_eastmoney_health,
    _check_price_health,
    _live_or_empty,
    calls,
    demand,
    edgar,
    etf_scale,
    fred,
    fund_holdings,
    fund_universe,
    fundamentals,
    positions,
    prices,
    themes,
    trading_activity,
)
from factor_scope.ingest.base import fetched_at_for, fetched_at_now, host_breaker
from factor_scope.ingest.feed import Feed, get_feed
from factor_scope.store import PointInTimeStore, Reading

logger = logging.getLogger(__name__)


class AShareUniverse:
    """The book — the local ``positions.csv`` plus the full fund universe behind it.

    The local positions file is the held seed; the feed supplies the whole fund universe + ETF
    scale, then every on-exchange ETF's holdings, daily trading activity, and valuation history is
    refreshed (so the look-through graph and the crowding/valuation surfaces rebuild from the
    universe's disclosures, not just the held book). The per-fund re-pulls are watermarked: each is
    handed the latest ``as_of`` already stored for its ``(series, key)``, so the universe-wide
    nightly re-pull fetches only newer observations (quadratic → linear); the whole-universe
    universe/scale snapshots stay full — delisting detection needs the complete membership list.
    """

    def gather(
        self,
        config: Config,
        *,
        as_of: str,
        fetched_at: str,
        feed: Feed,
        store: PointInTimeStore | None = None,
        deadline: IngestDeadline | None = None,
    ) -> list[Reading]:
        readings: list[Reading] = list(
            positions.load_fixture(
                config.fixtures_dir / positions.FIXTURE, as_of=as_of, fetched_at=fetched_at
            )
        )
        # Universe membership + ETF-scale snapshots run once, before the per-fund loop, feeding
        # delisting detection and the tier screen — so they have no safe empty fallback. They are
        # bounded (retry + per-attempt deadline) but not degraded: a hung host here is a loud,
        # bounded failure, not an unbounded stall (the run-level deadline can't fire synchronously).
        universe = _bounded(lambda: feed.universe(as_of=as_of, fetched_at=fetched_at))
        readings += universe
        readings += _bounded(lambda: feed.etf_scale(fetched_at=fetched_at))
        fetch_codes = _fetch_universe_codes(readings, as_of)
        activity_floor = _series_watermarks(store, trading_activity.SERIES, as_of)
        valuation_floor = _series_watermarks(store, fundamentals.SERIES, as_of)
        holdings_floor = _holdings_watermarks(store, as_of)
        for fund in universe:
            if deadline is not None and deadline.exceeded():
                logger.warning(
                    "ingest: wall-clock budget exceeded; stopping the per-fund universe loop early "
                    "(partial-but-valid — the funds reached this run keep their refreshed legs)"
                )
                break
            if fund.key in fetch_codes:  # core/probation ETFs → holdings, the look-through edges
                # Each per-fund leg runs behind the same resilience boundary as the price sources:
                # retry + a wall-clock deadline + a logged failover, so one fund's blocked or hung
                # source degrades that factor to invalid rather than aborting the universe loop.
                readings += _live_or_empty(
                    feed.holdings,
                    fund.key,
                    source=fund_holdings.SERIES,
                    fetched_at=fetched_at,
                    since=holdings_floor.get(fund.key),
                )
                readings += _live_or_empty(
                    feed.activity,
                    fund.key,
                    source=trading_activity.SERIES,
                    fetched_at=fetched_at,
                    since=activity_floor.get(fund.key),
                )
                readings += _live_or_empty(
                    feed.valuation,
                    fund.key,
                    source=fundamentals.SERIES,
                    fetched_at=fetched_at,
                    since=valuation_floor.get(fund.key),
                )
        return readings


def _fetch_universe_codes(readings: list[Reading], as_of: str) -> set[str]:
    """The on-exchange codes that earn the per-fund + deep-price fetch — all but the dead tier.

    The tier is a pure function of the cheap spot-board fields (AUM + traded value, both on the
    once-per-run ``etf_scale`` board) plus the fund's inception, so the whole universe is screened
    with no per-fund call: a seasoned, sub-floor, untraded zombie is dropped from the fetch set
    (still recorded in the universe/scale reads, just not deep-fetched), while core *and* the
    uncrowded probation candidates are kept — so discovery never goes blind to a small-but-improving
    fund. The watermark/incremental pull (Phase-1) then keeps each kept fund's nightly cost linear.
    """

    scale = {r.key: r.payload for r in readings if r.series == etf_scale.SERIES}
    fetch: set[str] = set()
    for r in readings:
        if r.series == fund_universe.SERIES and r.payload.get("on_exchange"):
            row = scale.get(r.key, {})
            tier = fund_universe.classify_tier(
                aum=row.get("aum"),
                amount=row.get("amount"),
                inception=r.payload.get("inception"),
                as_of=as_of,
            )
            if tier != "dead":
                fetch.add(r.key)
    return fetch


def _series_watermarks(store: PointInTimeStore | None, series: str, as_of: str) -> dict[str, str]:
    """The newest stored ``as_of`` per key in ``series`` knowable as of the run — the fetch floor.

    Keyed by the adapter's own key (a fund code for trading activity / valuation), so a re-pull
    starts just past what the append-only log already holds. Empty when there is no store to read
    (a standalone ``gather`` call), so the first pull takes full history.

    Provisional readings (a spot-board current-session estimate, not a settled history bar) are
    skipped at the store read (``excluding``), so a settled bar isn't hidden by a provisional one
    layered on top: an outage that fell back to the spot board leaves the floor on the last settled
    session, and the next history pull backfills the sessions it missed from there.
    """

    if store is None:
        return {}
    return {r.key: r.as_of for r in store.read_as_of(series, as_of, excluding="provisional")}


def _holdings_watermarks(store: PointInTimeStore | None, as_of: str) -> dict[str, str]:
    """The latest disclosed quarter per fund — holdings key is ``fund/holding``, grouped by fund."""

    if store is None:
        return {}
    latest: dict[str, str] = {}
    for r in store.read_as_of(fund_holdings.SERIES, as_of):
        fund = str(r.payload["fund"])
        if r.as_of > latest.get(fund, ""):
            latest[fund] = r.as_of
    return latest


class ASharePrices:
    """Point-in-time NAVs for the book and the on-exchange universe, reconciled across sources.

    The feed supplies each code's NAV history on three corroborating legs (AkShare/Baostock/Mootdx);
    :func:`_reconcile_history` reconciles them per date, so one scraper going offline can't kill the
    run and a same-day divergence is flagged (never fatal). The data circuit breaker trips only on a
    systemic divergence across the **book** (``required``), not on a universe fund that is simply
    unpriced (see ``_check_price_health``).
    """

    def gather(
        self,
        config: Config,
        codes: Sequence[str],
        *,
        as_of: str,
        fetched_at: str,
        feed: Feed,
        required: Sequence[str] | None = None,
        store: PointInTimeStore | None = None,
        deadline: IngestDeadline | None = None,
    ) -> list[Reading]:
        required_codes = set(codes if required is None else required)
        # Prices are watermarked like the other per-fund series: the cold pull seeds a ~400-day
        # window and each later night fetches only sessions past the store's floor.
        price_floor = _series_watermarks(store, prices.SERIES, as_of)
        # A book code already priced in the store is not "unpriced" tonight just because no *new*
        # bar arrived (an incremental re-pull is a no-op on a settled night); the breaker reasons
        # point-in-time so an incremental run doesn't trip on the codes it already holds.
        stored = {r.key for r in store.read_as_of(prices.SERIES, as_of)} if store else set()
        readings: list[Reading] = []
        degraded: list[str] = []  # book codes with no reconciled price — unpriced or flagged
        seen: set[str] = set()  # codes actually attempted (vs left unreached by a deadline trip)
        stopped_early = False
        for code in sorted(set(codes)):
            if deadline is not None and deadline.exceeded():
                stopped_early = True
                break
            seen.add(code)
            reconciled = _reconcile_history(
                feed.price_sources(code, fetched_at=fetched_at, since=price_floor.get(code)),
                tolerance=config.corroboration_tolerance,
            )
            readings += reconciled
            if code in required_codes:
                if reconciled and "divergence" in reconciled[-1].payload:
                    degraded.append(code)  # a same-day source disagreement, flagged
                elif not reconciled and code not in stored:
                    degraded.append(code)  # no fresh bar and none on record → genuinely unpriced
        if stopped_early:
            # A deadline cut the loop short. Book codes never reached are a *coverage* gap (ran
            # out of time), not a price *divergence* — surface them loudly but keep them out of the
            # breaker's fraction below, which reasons only over codes actually attempted. So the
            # breaker can't read a truncated run as "N/N corroborated", yet a tight deadline still
            # degrades partial-but-valid rather than raising.
            unreached = sorted(required_codes - seen - stored)
            logger.warning(
                "ingest: wall-clock budget exceeded; stopped the per-code price loop after %d "
                "code(s) — %d/%d required (book) codes left unpriced (deadline-truncated, "
                "partial-but-valid)",
                len(seen),
                len(unreached),
                len(required_codes),
            )
        _check_price_health(len(required_codes & seen), degraded)
        return readings


def _reconcile_history(sources: list[list[Reading]], *, tolerance: float) -> list[Reading]:
    """Reconcile one code's NAV history across the price legs, one bar per shared date.

    Each leg is that source's read (possibly empty, possibly multi-bar). For every date any leg
    discloses, the same-day cohort is reconciled by :func:`prices.select_reconciled` (fall-back /
    corroborate / flag / median), so the full reconciled history is rebuilt — a single latest bar
    live, the whole recorded series offline — through one policy.
    """

    dates = sorted({r.as_of for source in sources for r in source})
    reconciled: list[Reading] = []
    for date in dates:
        cohort = [[r] for source in sources for r in source if r.as_of == date]
        reconciled += prices.select_reconciled(cohort, tolerance=tolerance)
    return reconciled


class AShareThemes:
    """Emerging themes + their reference constituents (the funnel's Stage-A inputs + mapping seed).

    Fixtures load the bundled themes when present; each theme's candidate funds are *inferred* from
    its constituents downstream (holdings overlap + return correlation), not loaded from a tagged
    table. Live theme discovery (BERTopic / an LLM tagging pass) is the separate ``discover``
    service, not wired into this market, so the live path yields none here for now.
    """

    def gather(self, config: Config, *, as_of: str, fetched_at: str) -> list[Reading]:
        if config.source == "live":
            return []  # pragma: no cover - live path (theme discovery is the separate service)
        themes_path = config.fixtures_dir / themes.FIXTURE
        if not themes_path.exists():
            return []
        return themes.load_fixture(themes_path, fetched_at=fetched_at)


class CredentialError(RuntimeError):
    """A live leg's required credential is not set — a permanent operator error, fail fast.

    Distinct from a transient feed outage (degraded via ``_live_or_empty``, run continues): a
    missing credential never succeeds on retry, so it is caught before the expensive universe/price
    pull rather than discovered only after a multi-hour run leaves the dial degraded.
    """


def preflight_live_credentials(config: Config) -> None:
    """Fail fast if a live leg this config will attempt is missing its required credential.

    FRED is always attempted on live (``_gather_macro``); EDGAR only when ``config.edgar_ciks`` is
    non-empty (``_gather_edgar``); AkShare (``_gather_demand``) needs no key. Credentials are
    resolved the same way the adapters resolve them — env first, then the macOS Keychain
    (:func:`factor_scope.credentials.resolve_credential`) — so the interactive ``live-check`` and
    the launchd nightly preflight identically; they are deliberately not ``Config`` fields.
    """

    missing: list[str] = []
    if not resolve_credential("FRED_API_KEY"):
        missing.append("FRED_API_KEY (required for the macro dial)")
    if config.edgar_ciks and not resolve_credential("EDGAR_IDENTITY"):
        missing.append("EDGAR_IDENTITY (required because config.edgar_ciks is set)")
    if missing:
        raise CredentialError(
            "missing required live credential(s): "
            + "; ".join(missing)
            + " — set them in the environment before running (see docs/ops/RUNBOOK.md)"
        )


def _gather_macro(config: Config, *, fetched_at: str) -> list[Reading]:
    """The book-wide macro/liquidity dial (FRED). Fixtures load the bundle; live pulls defaults.

    Each live series returns only its latest observation, so the macro factor's history accrues over
    nights; the offline dial is the recorded multi-month window, loaded as the seed. Each series
    runs behind the resilience boundary (``_live_or_empty``) — a transient failure degrades it to
    no reading, logged, rather than aborting the run.
    """

    if config.source != "live":
        return fred.load_fixture(config.fixtures_dir / fred.FIXTURE, fetched_at=fetched_at)
    return [
        r
        for series_id in fred.DEFAULT_SERIES
        for r in _live_or_empty(
            fred.fetch_live, series_id, source=fred.SERIES, fetched_at=fetched_at
        )
    ]


def _demand_adapter(_code: str, *, fetched_at: str) -> list[Reading]:
    """Adapt the keyless, book-wide ``demand.fetch_live(*, fetched_at)`` to ``_live_or_empty``'s
    ``fetch(code, *, fetched_at, **kwargs)`` shape — end-demand has no per-item key to thread."""

    return demand.fetch_live(fetched_at=fetched_at)


def _gather_demand(config: Config, *, fetched_at: str) -> list[Reading]:
    """The book-wide end-demand dial (orders/capex revisions). Fixtures load it; live pulls.

    Runs behind the same resilience boundary as the per-fund legs (``_live_or_empty``): a transient
    failure degrades the dial to no reading, logged, rather than aborting the run.
    """

    if config.source != "live":
        return demand.load_fixture(config.fixtures_dir / demand.FIXTURE, fetched_at=fetched_at)
    return _live_or_empty(_demand_adapter, demand.KEY, source=demand.SERIES, fetched_at=fetched_at)


def _gather_edgar(config: Config, *, fetched_at: str) -> list[Reading]:
    """The US lead-chain (EDGAR). Fixtures load the filings; live pulls the configured CIKs.

    Live pulls only each filer's latest filing, so the cross-market factor's history accrues over
    nights; the offline feed is the recorded multi-quarter window, loaded as the seed. Each filer
    runs behind the same resilience boundary as the per-fund legs (``_live_or_empty``): a transient
    failure degrades just that filer to no reading, logged, rather than aborting the run.
    """

    if config.source != "live":
        path = config.fixtures_dir / edgar.FIXTURE
        return edgar.load_fixture(path, fetched_at=fetched_at) if path.exists() else []
    readings: list[Reading] = []
    for cik in config.edgar_ciks:
        readings += _live_or_empty(
            edgar.fetch_live, cik, source=edgar.SERIES, fetched_at=fetched_at, form="NPORT-P"
        )
    return readings


def _gather_prior_calls(config: Config, *, fetched_at: str) -> list[Reading]:
    """The engine's own prior leans — seed data for the offline self-scoring loop (fixtures only).

    In the durable nightly flow real leans accumulate from the digest, so nothing is ingested here.
    """

    if config.source == "live":
        return []  # pragma: no cover - live path
    path = config.fixtures_dir / calls.FIXTURE
    return calls.load_fixture(path, fetched_at=fetched_at) if path.exists() else []


@dataclass(frozen=True)
class AShareMarket:
    """The A-share market: the universe + reconciled prices, plus the macro/demand/EDGAR dials."""

    name: str = "ashare"

    def gather(
        self, config: Config, *, as_of: str, store: PointInTimeStore | None = None
    ) -> list[Reading]:
        # A live pull stamps the real wall-clock instant it was fetched (ops telemetry); a fixtures
        # pull derives a deterministic stamp from ``as_of`` so the offline artifact stays byte-for-
        # byte. Either way ``fetched_at`` never reaches the artifact (which is keyed on ``as_of``).
        fetched_at = fetched_at_now() if config.source == "live" else fetched_at_for(as_of)
        host_breaker.reset()  # the EastMoney breaker is run-scoped — last night's block can't leak
        # One run-level wall-clock budget spans the whole gather (None = unbounded, the offline
        # default), so a wedged source can't stall the nightly run past it — the per-fund/per-code
        # loops below stop once it trips, shipping the partial-but-valid readings gathered so far.
        deadline = IngestDeadline(config.ingest_deadline_seconds)
        # One store-aware feed for the whole run, threaded through both legs: it fetches the shared
        # spot board once and carries the store for the per-code load-shape decision.
        feed = get_feed(config, store)
        readings = AShareUniverse().gather(
            config, as_of=as_of, fetched_at=fetched_at, feed=feed, store=store, deadline=deadline
        )
        # Price the held book *and* the fetched (non-dead) universe: the funnel reasons over
        # candidate funds' NAVs (the trend gate, the launch-at-peak run-up, the return-correlation
        # mapping), not just the held codes — but a dead-tier zombie earns no deep-price pull, so
        # push2his burst shrinks with the per-fund loop. The book is the breaker's required set.
        book = [r.key for r in readings if r.series == positions.SERIES]
        codes = sorted(set(book) | _fetch_universe_codes(readings, as_of))
        readings += ASharePrices().gather(
            config,
            codes,
            as_of=as_of,
            fetched_at=fetched_at,
            feed=feed,
            required=book,
            store=store,
            deadline=deadline,
        )
        readings += AShareThemes().gather(config, as_of=as_of, fetched_at=fetched_at)
        readings += _gather_macro(config, fetched_at=fetched_at)
        readings += _gather_demand(config, fetched_at=fetched_at)
        readings += _gather_edgar(config, fetched_at=fetched_at)
        readings += _gather_prior_calls(config, fetched_at=fetched_at)
        _check_eastmoney_health()  # one run-level alarm if the K-line host blocked the burst
        return readings
