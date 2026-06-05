"""Unit tests for the rank-against-own-history banding (spec §03).

Bands are constant, economic-meaning cut-points on a percentile — never tuned to P&L.
"""

import pytest

from factor_scope.contract import Band
from factor_scope.factors.bands import percentile_rank, rank_to_band

pytestmark = pytest.mark.unit


def test_percentile_rank_is_midrank_fraction() -> None:
    sample = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert percentile_rank(4.0, sample) == pytest.approx(0.9)  # 4 below + half of the tie
    assert percentile_rank(0.0, sample) == pytest.approx(0.1)
    assert percentile_rank(2.0, sample) == pytest.approx(0.5)


def test_percentile_rank_handles_value_outside_sample() -> None:
    assert percentile_rank(10.0, [1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert percentile_rank(-10.0, [1.0, 2.0, 3.0]) == pytest.approx(0.0)


def test_rank_to_band_uses_constant_tail_cutpoints() -> None:
    assert rank_to_band(0.02) is Band.EXTREME_LOW
    assert rank_to_band(0.10) is Band.LOW
    assert rank_to_band(0.50) is Band.NEUTRAL
    assert rank_to_band(0.90) is Band.HIGH
    assert rank_to_band(0.98) is Band.EXTREME_HIGH


def test_percentile_rank_rejects_empty_sample() -> None:
    with pytest.raises(ValueError, match="empty"):
        percentile_rank(1.0, [])
