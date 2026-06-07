"""Unit tests for the rolling-window trim in the scorecard (spec §06).

The scorecard advertises a rolling 60-day window; it must *apply* it — trimming calls older than
the window before computing any statistic — so "an old regime can't rule a new one". The day count
is parsed from ``window`` itself, so the displayed label and the applied filter can never drift.
"""

import pytest

from factor_scope.contract import LeanAction
from factor_scope.scoring.calls import Call
from factor_scope.scoring.scorecard import build_scorecard, parse_window_days
from factor_scope.scoring.scorer import Outcome, ScoredCall

pytestmark = pytest.mark.unit

AS_OF = "2026-06-06"
CUTOFF = "2026-04-07"  # AS_OF - 60d: the oldest date still inside the window
JUST_OUTSIDE = "2026-04-06"  # one day older — must be trimmed


def _scored(as_of: str, confidence: float, outcome: Outcome, idx: int) -> ScoredCall:
    call = Call(
        call_id=f"c{idx}",
        code="X",
        as_of=as_of,
        action=LeanAction.BUY_EARLY,
        confidence=confidence,
        horizon_d=5,
    )
    return ScoredCall(call=call, outcome=outcome, fwd_ret=0.01, resolved_on=as_of)


def _perfect_inside(n: int) -> list[ScoredCall]:
    # n perfectly-calibrated in-window calls (conf 1.0, all hit) → Brier 0
    return [_scored(CUTOFF, 1.0, Outcome.HIT, i) for i in range(n)]


def test_parse_window_days_reads_the_day_count() -> None:
    assert parse_window_days("60d") == 60
    assert parse_window_days("7d") == 7


def test_call_outside_the_window_is_excluded_from_every_statistic() -> None:
    # 10 perfect in-window calls (Brier 0) plus one badly-missed call dated one day too old.
    inside = _perfect_inside(10)
    stale = _scored(JUST_OUTSIDE, 1.0, Outcome.MISS, 99)  # would push Brier up if counted
    card = build_scorecard(inside + [stale], AS_OF, min_n=2)
    assert card.n == 10  # the stale call is gone from the sample
    assert card.brier == 0.0  # …and from the Brier


def test_call_just_inside_the_window_is_included() -> None:
    inside = _perfect_inside(10)
    fresh = _scored(CUTOFF, 1.0, Outcome.MISS, 99)  # same bad call, dated exactly at the cutoff
    card = build_scorecard(inside + [fresh], AS_OF, min_n=2)
    assert card.n == 11  # counted
    assert card.brier is not None and card.brier > 0.0  # and it moves the Brier
