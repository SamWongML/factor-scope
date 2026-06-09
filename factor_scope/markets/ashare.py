"""A-share / funds-and-ETF market — the first concrete :class:`~factor_scope.markets.base.Market`.

Wraps the existing ingestion adapters behind the source protocols: the local ``positions.csv`` book
plus its disclosed holdings (:class:`AShareUniverse`), CN NAVs reconciled across AkShare/Baostock/
Mootdx (:class:`ASharePrices`), and the emerging-theme candidates (:class:`AShareThemes`). On top of
those three seams the market adds two book-wide reads that aren't market universe/price/theme data:
the FRED macro dial and the engine's own prior leans (seed data for the offline self-scoring loop).

Each source dispatches on ``config.source``: ``live`` (the default — heavy deps stay lazily imported
inside each adapter's ``fetch_live``) or ``fixtures`` (the offline test mode — bundled sample data,
so selecting the market never shells out on the fixtures path).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from factor_scope.config import Config
from factor_scope.ingest import (
    _check_price_health,
    _live_or_empty,
    baostock,
    calls,
    demand,
    edgar,
    etf_scale,
    fred,
    fund_holdings,
    fund_universe,
    fundamentals,
    mootdx,
    positions,
    prices,
    theme_funds,
    themes,
    trading_activity,
)
from factor_scope.ingest.base import fetched_at_for
from factor_scope.markets.base import ComposedMarket
from factor_scope.store import Reading


class AShareUniverse:
    """The book — the local ``positions.csv`` plus the full fund universe behind it.

    Fixtures load the bundled positions, the full fund universe (``fund_universe`` + ``etf_scale``),
    the fund holdings, each ETF's daily trading activity (turnover + traded value), and US (EDGAR)
    holdings. Live keeps the local positions file as the held seed but pulls the whole fund universe
    + ETF scale, then refreshes every on-exchange ETF's holdings and trading activity (so the
    look-through graph and the crowding surface rebuild from the universe's live disclosures) and
    each configured EDGAR filer.
    """

    def gather(self, config: Config, *, as_of: str, fetched_at: str) -> list[Reading]:
        readings: list[Reading] = list(
            positions.load_fixture(
                config.fixtures_dir / positions.FIXTURE, as_of=as_of, fetched_at=fetched_at
            )
        )
        if config.source == "fixtures":
            readings += fund_universe.load_fixture(
                config.fixtures_dir / fund_universe.FIXTURE, as_of=as_of, fetched_at=fetched_at
            )
            readings += etf_scale.load_fixture(
                config.fixtures_dir / etf_scale.FIXTURE, fetched_at=fetched_at
            )
            readings += fund_holdings.load_fixture(
                config.fixtures_dir / fund_holdings.FIXTURE, fetched_at=fetched_at
            )
            readings += trading_activity.load_fixture(
                config.fixtures_dir / trading_activity.FIXTURE, fetched_at=fetched_at
            )
            readings += fundamentals.load_fixture(
                config.fixtures_dir / fundamentals.FIXTURE, fetched_at=fetched_at
            )
            readings += edgar.load_fixture(
                config.fixtures_dir / edgar.FIXTURE, fetched_at=fetched_at
            )
            return readings
        universe = fund_universe.fetch_live(as_of=as_of, fetched_at=fetched_at)  # pragma: no cover
        readings += universe  # pragma: no cover - opt-in live path
        readings += etf_scale.fetch_live(fetched_at=fetched_at)  # pragma: no cover
        for fund in universe:  # pragma: no cover - opt-in live path
            if fund.payload["on_exchange"]:  # ETFs disclose holdings → the look-through graph edges
                readings += fund_holdings.fetch_live(fund.key, fetched_at=fetched_at)
                readings += trading_activity.fetch_live(fund.key, fetched_at=fetched_at)
                readings += fundamentals.fetch_live(fund.key, fetched_at=fetched_at)
        for cik in config.edgar_ciks:  # pragma: no cover - opt-in live path
            readings += edgar.fetch_live(cik, form="NPORT-P", fetched_at=fetched_at)
        return readings


class ASharePrices:
    """Point-in-time NAVs for the book's codes.

    Fixtures read the bundled ``prices.csv``. Live triple-sources each code (AkShare/Baostock/
    Mootdx) and reconciles to a corroborated NAV, so one scraper going offline can't kill the run;
    the data circuit breaker trips only on a systemic divergence (see ``_check_price_health``).
    """

    def gather(
        self, config: Config, codes: Sequence[str], *, as_of: str, fetched_at: str
    ) -> list[Reading]:
        if config.source == "fixtures":
            return prices.load_fixture(config.fixtures_dir / prices.FIXTURE, fetched_at=fetched_at)
        readings: list[Reading] = []
        degraded: list[str] = []  # codes with no reconciled price — unpriced or flagged divergent
        for code in codes:
            priced = prices.select_reconciled(
                [
                    _live_or_empty(
                        prices.fetch_live, code, source=prices.SOURCE, fetched_at=fetched_at
                    ),
                    _live_or_empty(
                        baostock.fetch_live, code, source=baostock.SOURCE, fetched_at=fetched_at
                    ),
                    _live_or_empty(
                        mootdx.fetch_live, code, source=mootdx.SOURCE, fetched_at=fetched_at
                    ),
                ],
                tolerance=config.corroboration_tolerance,
            )
            readings += priced
            if not priced or any("divergence" in r.payload for r in priced):
                degraded.append(code)
        _check_price_health(len(codes), degraded)
        return readings


class AShareThemes:
    """Emerging-theme candidates + their candidate funds (the funnel's Stage A/B inputs).

    Fixtures load the bundled themes and theme funds when present. Live theme discovery (BERTopic /
    an LLM tagging pass) is opt-in and not wired into CI, so the live path yields none for now.
    """

    def gather(self, config: Config, *, as_of: str, fetched_at: str) -> list[Reading]:
        if config.source != "fixtures":
            return []  # pragma: no cover - opt-in live path (theme discovery)
        readings: list[Reading] = []
        themes_path = config.fixtures_dir / themes.FIXTURE
        if themes_path.exists():
            readings += themes.load_fixture(themes_path, fetched_at=fetched_at)
        funds_path = config.fixtures_dir / theme_funds.FIXTURE
        if funds_path.exists():
            readings += theme_funds.load_fixture(funds_path, fetched_at=fetched_at)
        return readings


def _gather_macro(config: Config, *, fetched_at: str) -> list[Reading]:
    """The book-wide macro/liquidity dial (FRED). Fixtures load the bundle; live pulls defaults."""

    if config.source == "fixtures":
        return fred.load_fixture(config.fixtures_dir / fred.FIXTURE, fetched_at=fetched_at)
    return [  # pragma: no cover - opt-in live path
        r
        for series_id in fred.DEFAULT_SERIES
        for r in fred.fetch_live(series_id, fetched_at=fetched_at)
    ]


def _gather_demand(config: Config, *, fetched_at: str) -> list[Reading]:
    """The book-wide end-demand dial (orders/capex revisions). Fixtures load it; live pulls."""

    if config.source == "fixtures":
        return demand.load_fixture(config.fixtures_dir / demand.FIXTURE, fetched_at=fetched_at)
    return demand.fetch_live(fetched_at=fetched_at)  # pragma: no cover - opt-in live path


def _gather_prior_calls(config: Config, *, fetched_at: str) -> list[Reading]:
    """The engine's own prior leans — seed data for the offline self-scoring loop (fixtures only).

    In the durable nightly flow real leans accumulate from the digest, so nothing is ingested here.
    """

    if config.source != "fixtures":
        return []  # pragma: no cover - opt-in live path
    path = config.fixtures_dir / calls.FIXTURE
    return calls.load_fixture(path, fetched_at=fetched_at) if path.exists() else []


@dataclass(frozen=True)
class AShareMarket:
    """The A-share market: the three sources composed, plus the macro dial and prior-call seed."""

    name: str = "ashare"

    def gather(self, config: Config, *, as_of: str) -> list[Reading]:
        composed = ComposedMarket(
            name=self.name,
            universe=AShareUniverse(),
            prices=ASharePrices(),
            themes=AShareThemes(),
        )
        readings = composed.gather(config, as_of=as_of)
        fetched_at = fetched_at_for(as_of)
        readings += _gather_macro(config, fetched_at=fetched_at)
        readings += _gather_demand(config, fetched_at=fetched_at)
        readings += _gather_prior_calls(config, fetched_at=fetched_at)
        return readings
