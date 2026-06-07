"""Unit tests for the scorecard math: Brier score + Brier skill score.

Hand-computed cases: a perfect forecaster scores 0, the worst scores 1, and the skill score is
measured against the base-rate forecaster (predict the overall hit-rate every time).
"""

import pytest

from factor_scope.scoring.scorecard import base_rate, brier, brier_skill_score

pytestmark = pytest.mark.unit


def test_perfect_forecaster_scores_zero() -> None:
    pairs = [(1.0, True), (1.0, True), (0.0, False), (0.0, False)]
    assert brier(pairs) == 0.0


def test_worst_forecaster_scores_one() -> None:
    pairs = [(1.0, False), (0.0, True), (1.0, False), (0.0, True)]
    assert brier(pairs) == 1.0


def test_coin_flip_at_half_scores_quarter() -> None:
    pairs = [(0.5, True), (0.5, False)]
    assert brier(pairs) == 0.25


def test_base_rate_is_the_hit_fraction() -> None:
    assert base_rate([True, True, False, False]) == 0.5
    assert base_rate([True, True, True, False]) == 0.75


def test_skill_is_one_when_perfect_against_a_split_base_rate() -> None:
    # base rate 0.5 → reference Brier 0.25; a perfect model → Brier 0 → skill 1.0
    pairs = [(1.0, True), (1.0, True), (0.0, False), (0.0, False)]
    assert brier_skill_score(pairs) == pytest.approx(1.0)


def test_skill_is_zero_when_no_better_than_the_base_rate() -> None:
    # predict the base rate (0.5) every time → same Brier as the reference → skill 0.0
    pairs = [(0.5, True), (0.5, True), (0.5, False), (0.5, False)]
    assert brier_skill_score(pairs) == pytest.approx(0.0)


def test_skill_is_none_when_base_rate_is_degenerate() -> None:
    # all outcomes identical → the base-rate forecaster is already perfect → skill undefined
    pairs = [(0.6, True), (0.7, True), (0.9, True)]
    assert brier_skill_score(pairs) is None
