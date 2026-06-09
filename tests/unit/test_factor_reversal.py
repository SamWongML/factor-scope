"""Unit tests for the reversal state.

Short-horizon return ranked vs its own history. In A-shares a stretched up-move is a
reversal-**DOWN** risk; a hard sell-off is reversal-**UP** potential.
"""

import pytest

from factor_scope.contract import Band
from factor_scope.factors import FactorContext
from factor_scope.factors.battery import reversal
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _ctx(code: str, navs: list[float], turnovers: list[float] | None = None) -> FactorContext:
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="prices",
                key=code,
                as_of=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"nav": nav},
            )
            for i, nav in enumerate(navs)
        ]
    )
    if turnovers is not None:
        store.append(
            [
                Reading(
                    series="trading_activity",
                    key=code,
                    as_of=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                    fetched_at="2026-06-05T22:00:00Z",
                    payload={"turnover": t, "amount": 2.0},
                )
                for i, t in enumerate(turnovers)
            ]
        )
    return FactorContext(code=code, as_of="2026-12-31", store=store)


def test_sharp_recent_runup_is_reversal_down_risk() -> None:
    navs = [1.0 + 0.001 * i for i in range(80)] + [1.08 + 0.05 * j for j in range(20)]
    state = reversal(_ctx("UP", navs))
    assert state.valid is True
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "DOWN" in state.direction


def test_sharp_recent_selloff_is_reversal_up_potential() -> None:
    navs = [2.0 - 0.001 * i for i in range(80)] + [1.92 - 0.05 * j for j in range(20)]
    state = reversal(_ctx("DN", navs))
    assert state.valid is True
    assert state.level in (Band.LOW, Band.EXTREME_LOW)
    assert "UP" in state.direction


def test_runup_on_heavy_turnover_is_a_confirmed_reversal_down() -> None:
    # a sharp run-up whose latest turnover sits in the top quartile of its own history → the band
    # (return rank) is qualified into a *confirmed* exhaustion, and the Amihud figure is surfaced.
    navs = [1.0 + 0.001 * i for i in range(80)] + [1.08 + 0.05 * j for j in range(20)]
    turns = [1.0 + 0.1 * i for i in range(15)] + [12.0]  # last reading is the heaviest
    state = reversal(_ctx("UP", navs, turnovers=turns))
    assert state.level in (Band.HIGH, Band.EXTREME_HIGH)
    assert "heavy turnover" in state.direction
    assert "reversal-DOWN" in state.direction
    assert "turnover pctile" in (state.evidence or "")
    assert "Amihud" in (state.evidence or "")


def test_reversal_without_turnover_stays_a_price_only_read() -> None:
    # the turnover/Amihud qualifier is optional: absent it, the band still reads from price alone.
    navs = [1.0 + 0.001 * i for i in range(80)] + [1.08 + 0.05 * j for j in range(20)]
    state = reversal(_ctx("UP", navs))
    assert state.valid is True
    assert "DOWN" in state.direction
    assert "Amihud" not in (state.evidence or "")


def test_violent_runup_lands_extreme_high() -> None:
    # the final 20-day return is the unique top of the whole return history → strictly EXTREME_HIGH.
    navs = [1.0 + 0.001 * i for i in range(80)] + [1.08 + 0.05 * j for j in range(20)]
    state = reversal(_ctx("UP", navs))
    assert state.level is Band.EXTREME_HIGH


def test_too_short_series_is_invalid_not_an_error() -> None:
    state = reversal(_ctx("S", [1.0, 1.1, 1.2, 1.3]))
    assert state.valid is False
