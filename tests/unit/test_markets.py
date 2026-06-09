"""The market seam — config-selected adapters behind source protocols.

The ``Market`` / ``UniverseSource`` / ``ThemeSource`` / ``PriceSource`` seams keep the engine
no longer hard-wired to A-share. A-share is the first concrete adapter; these pin the seam: the
market is selected by name, a fake market made of fake sources drives the whole pipeline, and the
A-share fixture gather still produces every series the artifact reads (no behaviour change).
"""

from __future__ import annotations

import pytest

from factor_scope.config import Config
from factor_scope.contract import ListName
from factor_scope.markets import ComposedMarket, get_market
from factor_scope.pipeline import build_dashboard
from factor_scope.store import Reading

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"


def test_get_market_selects_ashare_by_name() -> None:
    market = get_market("ashare")
    assert market.name == "ashare"


def test_get_market_rejects_an_unknown_market() -> None:
    with pytest.raises(ValueError, match="unknown market"):
        get_market("nyse")


def test_config_defaults_to_the_ashare_market() -> None:
    assert Config().market == "ashare"


def test_ashare_fixture_gather_keeps_every_series() -> None:
    # No behaviour change: the A-share adapter must still emit every series the artifact reads, so a
    # fixtures run produces the same store the prior monolithic gather did.
    readings = get_market("ashare").gather(Config(), as_of=AS_OF)
    series = {r.series for r in readings}
    assert series == {
        "positions",
        "fund_universe",
        "etf_scale",
        "prices",
        "fund_holdings",
        "fred",
        "edgar",
        "calls",
        "themes",
        "theme_funds",
    }
    # positions are stamped with the run's as_of (point-in-time), prices keep their own dates
    assert all(r.as_of == AS_OF for r in readings if r.series == "positions")


class _FakeUniverse:
    """A one-position book, in the UniverseSource shape."""

    def gather(self, config: Config, *, as_of: str, fetched_at: str) -> list[Reading]:
        return [
            Reading(
                series="positions",
                key="FAKE1",
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "name": "Fake Fund",
                    "cost_basis": 1.0,
                    "shares": 100.0,
                    "list": "holdings",
                },
            )
        ]


class _FakePrices:
    """One NAV for the universe's codes, in the PriceSource shape."""

    def gather(
        self, config: Config, codes: list[str], *, as_of: str, fetched_at: str
    ) -> list[Reading]:
        return [
            Reading(
                series="prices",
                key=code,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={"nav": 1.5, "source": "fake"},
            )
            for code in codes
        ]


class _FakeThemes:
    """No emerging themes, in the ThemeSource shape."""

    def gather(self, config: Config, *, as_of: str, fetched_at: str) -> list[Reading]:
        return []


def test_a_fake_market_drives_the_whole_pipeline() -> None:
    # The acceptance seam: a market composed of fake sources runs build_dashboard end to end and
    # yields a valid artifact — proving the pipeline targets the protocols, not A-share.
    market = ComposedMarket(
        name="fake", universe=_FakeUniverse(), prices=_FakePrices(), themes=_FakeThemes()
    )
    dash = build_dashboard(Config(), market=market)
    assert [item.item for item in dash.items] == ["Fake Fund"]
    item = dash.items[0]
    assert item.list_name is ListName.HOLDINGS
    assert item.gain == pytest.approx(0.5)  # (1.5 nav - 1.0 cost) / 1.0
