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

from collections.abc import Sequence
from dataclasses import dataclass

from factor_scope.config import Config
from factor_scope.ingest import (
    _check_price_health,
    _live_or_empty,
    calls,
    demand,
    edgar,
    fred,
    fund_holdings,
    fund_universe,
    fundamentals,
    positions,
    prices,
    themes,
    trading_activity,
)
from factor_scope.ingest.base import fetched_at_for
from factor_scope.ingest.feed import Feed, get_feed
from factor_scope.store import PointInTimeStore, Reading


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
        self, config: Config, *, as_of: str, fetched_at: str, store: PointInTimeStore | None = None
    ) -> list[Reading]:
        feed: Feed = get_feed(config)
        readings: list[Reading] = list(
            positions.load_fixture(
                config.fixtures_dir / positions.FIXTURE, as_of=as_of, fetched_at=fetched_at
            )
        )
        universe = feed.universe(as_of=as_of, fetched_at=fetched_at)
        readings += universe
        readings += feed.etf_scale(fetched_at=fetched_at)
        activity_floor = _series_watermarks(store, trading_activity.SERIES, as_of)
        valuation_floor = _series_watermarks(store, fundamentals.SERIES, as_of)
        holdings_floor = _holdings_watermarks(store, as_of)
        for fund in universe:
            if fund.payload["on_exchange"]:  # ETFs disclose holdings → the look-through graph edges
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


def _series_watermarks(store: PointInTimeStore | None, series: str, as_of: str) -> dict[str, str]:
    """The newest stored ``as_of`` per key in ``series`` knowable as of the run — the fetch floor.

    Keyed by the adapter's own key (a fund code for trading activity / valuation), so a re-pull
    starts just past what the append-only log already holds. Empty when there is no store to read
    (a standalone ``gather`` call), so the first pull takes full history.

    Provisional readings (a spot-board current-session estimate, not a settled history bar) are
    skipped, so an outage that fell back to the spot board does not advance the floor — the next
    history pull backfills the sessions it missed instead of starting past them.
    """

    if store is None:
        return {}
    return {
        r.key: r.as_of
        for r in store.read_as_of(series, as_of)
        if not r.payload.get("provisional")
    }


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
        required: Sequence[str] | None = None,
    ) -> list[Reading]:
        feed: Feed = get_feed(config)
        required_codes = set(codes if required is None else required)
        readings: list[Reading] = []
        degraded: list[str] = []  # book codes with no reconciled price — unpriced or flagged
        for code in sorted(set(codes)):
            reconciled = _reconcile_history(
                feed.price_sources(code, fetched_at=fetched_at),
                tolerance=config.corroboration_tolerance,
            )
            readings += reconciled
            if code in required_codes and (
                not reconciled or "divergence" in reconciled[-1].payload
            ):
                degraded.append(code)
        _check_price_health(len(required_codes), degraded)
        return readings


def _reconcile_history(
    sources: list[list[Reading]], *, tolerance: float
) -> list[Reading]:
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


def _gather_macro(config: Config, *, fetched_at: str) -> list[Reading]:
    """The book-wide macro/liquidity dial (FRED). Fixtures load the bundle; live pulls defaults.

    Each live series returns only its latest observation, so the macro factor's history accrues over
    nights; the offline dial is the recorded multi-month window, loaded as the seed.
    """

    if config.source != "live":
        return fred.load_fixture(config.fixtures_dir / fred.FIXTURE, fetched_at=fetched_at)
    return [  # pragma: no cover - live path
        r
        for series_id in fred.DEFAULT_SERIES
        for r in fred.fetch_live(series_id, fetched_at=fetched_at)
    ]


def _gather_demand(config: Config, *, fetched_at: str) -> list[Reading]:
    """The book-wide end-demand dial (orders/capex revisions). Fixtures load it; live pulls."""

    if config.source != "live":
        return demand.load_fixture(config.fixtures_dir / demand.FIXTURE, fetched_at=fetched_at)
    return demand.fetch_live(fetched_at=fetched_at)  # pragma: no cover - live path


def _gather_edgar(config: Config, *, fetched_at: str) -> list[Reading]:
    """The US lead-chain (EDGAR). Fixtures load the filings; live pulls the configured CIKs.

    Live pulls only each filer's latest filing, so the cross-market factor's history accrues over
    nights; the offline feed is the recorded multi-quarter window, loaded as the seed.
    """

    if config.source != "live":
        path = config.fixtures_dir / edgar.FIXTURE
        return edgar.load_fixture(path, fetched_at=fetched_at) if path.exists() else []
    readings: list[Reading] = []
    for cik in config.edgar_ciks:
        readings += edgar.fetch_live(cik, form="NPORT-P", fetched_at=fetched_at)
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
        fetched_at = fetched_at_for(as_of)
        readings = AShareUniverse().gather(
            config, as_of=as_of, fetched_at=fetched_at, store=store
        )
        # Price the held book *and* the on-exchange universe: the funnel reasons over candidate
        # funds' NAVs (the trend gate, the launch-at-peak run-up, the return-correlation mapping),
        # not just the held codes. The book is the circuit breaker's required set.
        book = [r.key for r in readings if r.series == positions.SERIES]
        on_exchange = [
            r.key
            for r in readings
            if r.series == fund_universe.SERIES and r.payload.get("on_exchange")
        ]
        codes = sorted(set(book) | set(on_exchange))
        readings += ASharePrices().gather(
            config, codes, as_of=as_of, fetched_at=fetched_at, required=book
        )
        readings += AShareThemes().gather(config, as_of=as_of, fetched_at=fetched_at)
        readings += _gather_macro(config, fetched_at=fetched_at)
        readings += _gather_demand(config, fetched_at=fetched_at)
        readings += _gather_edgar(config, fetched_at=fetched_at)
        readings += _gather_prior_calls(config, fetched_at=fetched_at)
        return readings
