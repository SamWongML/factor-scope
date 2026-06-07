"""Ingestion — the adapters that fill the point-in-time store.

``gather_fixture_readings`` runs every adapter's offline fixture backend (the default, deterministic
path used by tests and ``--fixtures``). ``gather_live_readings`` is the opt-in ``--live`` path: it
takes the universe from the local ``positions.csv`` and pulls live prices + the macro dial. Live
backends lazily import their heavy dependencies and are never exercised in CI.
"""

from __future__ import annotations

from factor_scope.config import Config
from factor_scope.ingest import (
    calls,
    edgar,
    fred,
    fund_holdings,
    positions,
    prices,
    theme_funds,
    themes,
)
from factor_scope.ingest.base import IngestError, fetched_at_for
from factor_scope.store import Reading

__all__ = ["IngestError", "gather_fixture_readings", "gather_live_readings"]


def gather_fixture_readings(config: Config, *, as_of: str) -> list[Reading]:
    """Every adapter's fixture rows, stamped deterministically — the offline default."""

    fetched_at = fetched_at_for(as_of)
    root = config.fixtures_dir
    readings: list[Reading] = []
    readings += positions.load_fixture(root / positions.FIXTURE, as_of=as_of, fetched_at=fetched_at)
    readings += prices.load_fixture(root / prices.FIXTURE, fetched_at=fetched_at)
    readings += fund_holdings.load_fixture(root / fund_holdings.FIXTURE, fetched_at=fetched_at)
    readings += fred.load_fixture(root / fred.FIXTURE, fetched_at=fetched_at)
    readings += edgar.load_fixture(root / edgar.FIXTURE, fetched_at=fetched_at)
    calls_fixture = root / calls.FIXTURE
    if calls_fixture.exists():  # prior falsifiable leans for the self-scoring loop
        readings += calls.load_fixture(calls_fixture, fetched_at=fetched_at)
    themes_fixture = root / themes.FIXTURE
    if themes_fixture.exists():  # candidate industries for the emerging funnel
        readings += themes.load_fixture(themes_fixture, fetched_at=fetched_at)
    theme_funds_fixture = root / theme_funds.FIXTURE
    if theme_funds_fixture.exists():  # candidate funds the funnel screens to a top 3
        readings += theme_funds.load_fixture(theme_funds_fixture, fetched_at=fetched_at)
    return readings


def gather_live_readings(  # pragma: no cover - opt-in
    config: Config, *, as_of: str
) -> list[Reading]:
    """The opt-in ``--live`` path: local positions + live NAV per holding + the FRED macro dial."""

    fetched_at = fetched_at_for(as_of)
    book = positions.load_fixture(
        config.fixtures_dir / positions.FIXTURE, as_of=as_of, fetched_at=fetched_at
    )
    readings: list[Reading] = list(book)
    for pos in book:
        readings += prices.fetch_live(pos.key, fetched_at=fetched_at)
    for series_id in fred.DEFAULT_SERIES:
        readings += fred.fetch_live(series_id, fetched_at=fetched_at)
    return readings
