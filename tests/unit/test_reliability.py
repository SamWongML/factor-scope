"""Unit tests for reliability-by-confidence bucketing.

Calls are grouped by stated-confidence bucket; each bucket's realised hit-rate is compared to the
stated confidence to flag over/under-confidence. A bucket with too few samples is hidden so noise
cannot masquerade as miscalibration.
"""

import pytest

from factor_scope.contract import ReliabilityBucket
from factor_scope.scoring.scorecard import reliability_buckets

pytestmark = pytest.mark.unit


def _pairs(conf: float, n_hit: int, n_miss: int) -> list[tuple[float, bool]]:
    return [(conf, True)] * n_hit + [(conf, False)] * n_miss


def test_overconfident_bucket_is_flagged() -> None:
    # stated 0.9 but only half land → overconfident
    buckets = reliability_buckets(_pairs(0.9, 5, 5), bucket_min_n=2, tol=0.1)
    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket.bucket == 0.9
    assert bucket.realised == 0.5
    assert bucket.note == "overconfident"


def test_underconfident_bucket_is_flagged() -> None:
    # stated 0.3 but most land → underconfident
    buckets = reliability_buckets(_pairs(0.3, 9, 1), bucket_min_n=2, tol=0.1)
    assert buckets[0].note == "underconfident"


def test_well_calibrated_bucket_has_no_note() -> None:
    buckets = reliability_buckets(_pairs(0.7, 7, 3), bucket_min_n=2, tol=0.1)
    assert buckets[0].bucket == 0.7
    assert buckets[0].realised == pytest.approx(0.7)
    assert buckets[0].note is None


def test_min_sample_gating_hides_thin_buckets() -> None:
    # one lonely 0.3 call (below the min) is dropped; the well-populated 0.9 bucket survives
    pairs = _pairs(0.9, 4, 4) + [(0.3, True)]
    buckets = reliability_buckets(pairs, bucket_min_n=2, tol=0.1)
    assert [b.bucket for b in buckets] == [0.9]


def test_confidences_snap_to_the_nearest_tenth() -> None:
    # 0.88 and 0.92 both fall in the 0.9 bucket
    pairs = [(0.88, True), (0.92, False)]
    buckets = reliability_buckets(pairs, bucket_min_n=2, tol=0.1)
    assert len(buckets) == 1
    assert buckets[0].bucket == 0.9
    assert isinstance(buckets[0], ReliabilityBucket)
