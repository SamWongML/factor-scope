"""Market seams — the protocols a market's data sources drop in behind.

A :class:`Market` turns one run (a :class:`~factor_scope.config.Config` + an ``as_of``) into the
:class:`~factor_scope.store.Reading` rows the pipeline reasons over. It is composed of three
substitutable sources — a :class:`UniverseSource` (the book), a :class:`PriceSource` (NAVs), and a
:class:`ThemeSource` (emerging candidates) — so the engine targets these interfaces, not A-share.
:class:`ComposedMarket` is the generic wiring (universe → its codes → prices → themes) a market can
reuse; a concrete market may instead orchestrate its own gather (A-share prices its on-exchange
universe, not just the book, so it composes the sources directly — see
:mod:`factor_scope.markets.ashare`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from factor_scope.config import Config
from factor_scope.ingest.base import fetched_at_for
from factor_scope.ingest.positions import SERIES as POSITIONS_SERIES
from factor_scope.store import PointInTimeStore, Reading


@runtime_checkable
class UniverseSource(Protocol):
    """Yields the run's book — the ``positions`` rows that seed the three lists."""

    def gather(
        self, config: Config, *, as_of: str, fetched_at: str, store: PointInTimeStore | None = None
    ) -> list[Reading]: ...


@runtime_checkable
class PriceSource(Protocol):
    """Yields point-in-time NAVs for the universe's ``codes`` (the ``prices`` rows)."""

    def gather(
        self, config: Config, codes: Sequence[str], *, as_of: str, fetched_at: str
    ) -> list[Reading]: ...


@runtime_checkable
class ThemeSource(Protocol):
    """Yields candidate themes + their candidate funds for the emerging funnel."""

    def gather(self, config: Config, *, as_of: str, fetched_at: str) -> list[Reading]: ...


@runtime_checkable
class Market(Protocol):
    """A named market: the single seam the pipeline calls to fill the store for a run."""

    @property
    def name(self) -> str:  # read-only, so a frozen dataclass field satisfies it
        ...

    def gather(
        self, config: Config, *, as_of: str, store: PointInTimeStore | None = None
    ) -> list[Reading]: ...


@dataclass(frozen=True)
class ComposedMarket:
    """A market wired from a universe, a price, and a theme source.

    ``gather`` runs the universe first, takes its position codes, prices exactly those, then adds
    the theme candidates — the order is immaterial to the artifact (the store reads point-in-time by
    key), so this is just the dependency: prices need the universe's codes.
    """

    name: str
    universe: UniverseSource
    prices: PriceSource
    themes: ThemeSource

    def gather(
        self, config: Config, *, as_of: str, store: PointInTimeStore | None = None
    ) -> list[Reading]:
        fetched_at = fetched_at_for(as_of)
        readings = list(
            self.universe.gather(config, as_of=as_of, fetched_at=fetched_at, store=store)
        )
        codes = [r.key for r in readings if r.series == POSITIONS_SERIES]
        readings += self.prices.gather(config, codes, as_of=as_of, fetched_at=fetched_at)
        readings += self.themes.gather(config, as_of=as_of, fetched_at=fetched_at)
        return readings
