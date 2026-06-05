"""Unit tests for the emerging funnel's Stage A — qualify the *industry* (spec §07).

Stage A is a sequence of hard gates over descriptive theme inputs (no fitted composite): signal
strength (acceleration + breadth − crowding), durability (broad adoption + path to profit +
fad-resistance), lead-chain corroboration, and an investable wrapper. A theme must clear *every*
gate to advance to Stage B; the first failing gate is reported so the stop is auditable.
"""

from __future__ import annotations

import pytest

from factor_scope.emerging.stage_a import Theme, qualify_theme, signal_strength

pytestmark = pytest.mark.unit


def _theme(**overrides: object) -> Theme:
    """A durable, corroborated, investable theme (clears Stage A) with per-field overrides."""

    base: dict[str, object] = dict(
        name="储能",
        acceleration=0.62,
        base_level=0.30,
        breadth=6,
        crowding=0.35,
        broad_adoption=True,
        path_to_profit=True,
        fad_resistant=True,
        lead_chain=True,
        wrapper_exists=True,
        as_of="2026-05-31",
    )
    base.update(overrides)
    return Theme(**base)  # type: ignore[arg-type]


def test_strong_durable_theme_clears_stage_a() -> None:
    result = qualify_theme(_theme())
    assert result.passed is True
    assert result.failed_test is None
    assert result.theme == "储能"
    assert result.signal_strength == pytest.approx(0.62 + 1.0 - 0.35)


def test_a_fad_stops_on_durability() -> None:
    # High acceleration but it fails the decisive durability filter (a one-cycle fad).
    result = qualify_theme(_theme(name="元宇宙", acceleration=0.70, fad_resistant=False))
    assert result.passed is False
    assert result.failed_test == "durability"


def test_no_investable_wrapper_stops_the_funnel() -> None:
    # Durable + corroborated, but there is no fund/ETF to express it → watch-only, stop here.
    result = qualify_theme(_theme(name="可控核聚变", wrapper_exists=False))
    assert result.passed is False
    assert result.failed_test == "wrapper"


def test_a_domestic_narrative_without_lead_chain_stops() -> None:
    result = qualify_theme(_theme(lead_chain=False))
    assert result.passed is False
    assert result.failed_test == "lead_chain"


def test_a_weak_or_crowded_signal_stops_first() -> None:
    # Crowding eats the signal below the floor → fails the very first gate.
    result = qualify_theme(_theme(acceleration=0.41, breadth=1, crowding=0.9))
    assert result.passed is False
    assert result.failed_test == "signal"


def test_low_acceleration_alone_fails_the_signal_gate() -> None:
    # Acceleration is the most important sub-signal — a low read fails even with rich breadth.
    result = qualify_theme(_theme(acceleration=0.2, breadth=8, crowding=0.0))
    assert result.passed is False
    assert result.failed_test == "signal"


def test_signal_strength_is_acceleration_plus_breadth_minus_crowding() -> None:
    theme = _theme(acceleration=0.5, breadth=3, crowding=0.2)  # breadth 3/6 = 0.5
    assert signal_strength(theme) == pytest.approx(0.5 + 0.5 - 0.2)
