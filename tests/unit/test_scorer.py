"""Unit tests for the mechanical next-day scorer (spec §06).

Each lean is a falsifiable claim. Scoring is pure mechanics: forward return over the horizon vs the
lean's stated direction → hit / miss / abstain. No LLM, no memory, no opinion. A resolved call is
immutable — re-scoring after more data arrives never changes a settled outcome (point-in-time).
"""

import pytest

from factor_scope.contract import LeanAction
from factor_scope.scoring.calls import Call, log_call, read_calls
from factor_scope.scoring.scorer import (
    Outcome,
    classify_outcome,
    lean_direction,
    score_call,
    score_calls,
)
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit


def _prices(code: str, start: str, navs: list[float]) -> list[Reading]:
    # one observation per calendar day from `start`, oldest first
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [
        Reading(
            series="prices",
            key=code,
            as_of=(d0 + timedelta(days=i)).isoformat(),
            fetched_at="t",
            payload={"nav": nav},
        )
        for i, nav in enumerate(navs)
    ]


def _call(action: LeanAction, conf: float = 0.7, horizon: int = 10) -> Call:
    return Call(
        call_id=f"c-{action.value}",
        code="X",
        as_of="2026-01-11",
        action=action,
        confidence=conf,
        horizon_d=horizon,
        state_pattern=("trend:open",),
    )


def test_lean_direction_maps_each_action() -> None:
    assert lean_direction(LeanAction.BUY_EARLY) == 1
    assert lean_direction(LeanAction.HOLD) == 0
    assert lean_direction(LeanAction.TRIM) == -1
    assert lean_direction(LeanAction.EXIT) == -1
    assert lean_direction(LeanAction.AVOID) == -1
    assert lean_direction(LeanAction.ABSTAIN) is None


def test_classify_outcome_on_direction_and_return() -> None:
    assert classify_outcome(1, 0.05) is Outcome.HIT
    assert classify_outcome(1, -0.05) is Outcome.MISS
    assert classify_outcome(-1, -0.05) is Outcome.HIT
    assert classify_outcome(-1, 0.05) is Outcome.MISS
    # hold predicts a flat tape: a tiny move is a hit, a big move a miss
    assert classify_outcome(0, 0.001) is Outcome.HIT
    assert classify_outcome(0, 0.05) is Outcome.MISS


def test_buy_early_hits_when_price_rises() -> None:
    store = DuckDBStore(":memory:")
    store.append(_prices("X", "2026-01-01", [1.0 + 0.01 * i for i in range(40)]))
    scored = score_call(_call(LeanAction.BUY_EARLY), store, "2026-03-01")
    assert scored is not None
    assert scored.outcome is Outcome.HIT
    assert scored.fwd_ret is not None and scored.fwd_ret > 0


def test_exit_misses_when_price_rises() -> None:
    store = DuckDBStore(":memory:")
    store.append(_prices("X", "2026-01-01", [1.0 + 0.01 * i for i in range(40)]))
    scored = score_call(_call(LeanAction.EXIT), store, "2026-03-01")
    assert scored is not None
    assert scored.outcome is Outcome.MISS


def test_abstain_makes_no_claim() -> None:
    store = DuckDBStore(":memory:")
    store.append(_prices("X", "2026-01-01", [1.0 + 0.01 * i for i in range(40)]))
    scored = score_call(_call(LeanAction.ABSTAIN), store, "2026-03-01")
    assert scored is not None
    assert scored.outcome is Outcome.ABSTAIN
    assert scored.fwd_ret is None


def test_unresolved_call_is_pending() -> None:
    store = DuckDBStore(":memory:")
    store.append(_prices("X", "2026-01-01", [1.0 + 0.01 * i for i in range(40)]))
    # horizon not yet elapsed as of the run date → not scorable
    pending = score_call(_call(LeanAction.BUY_EARLY, horizon=10), store, "2026-01-12")
    assert pending is None


def test_resolved_call_is_immutable_under_more_data() -> None:
    store = DuckDBStore(":memory:")
    store.append(_prices("X", "2026-01-01", [1.0 + 0.01 * i for i in range(40)]))
    first = score_call(_call(LeanAction.BUY_EARLY), store, "2026-03-01")
    assert first is not None
    # later, more (even sharply different) prices arrive beyond the resolution window
    store.append(_prices("X", "2026-02-10", [5.0 - 0.1 * i for i in range(40)]))
    second = score_call(_call(LeanAction.BUY_EARLY), store, "2026-06-01")
    assert second is not None
    assert (second.outcome, second.fwd_ret, second.resolved_on) == (
        first.outcome,
        first.fwd_ret,
        first.resolved_on,
    )


def test_log_and_read_calls_round_trip_point_in_time() -> None:
    store = DuckDBStore(":memory:")
    log_call(store, _call(LeanAction.BUY_EARLY), fetched_at="t")
    log_call(
        store,
        Call(
            call_id="future",
            code="X",
            as_of="2026-09-01",
            action=LeanAction.HOLD,
            confidence=0.5,
            horizon_d=10,
            state_pattern=(),
        ),
        fetched_at="t",
    )
    # as of mid-year the future call is not yet knowable
    seen = read_calls(store, "2026-06-01")
    assert [c.call_id for c in seen] == ["c-buy_early"]


def test_score_calls_skips_pending_and_keeps_resolved() -> None:
    store = DuckDBStore(":memory:")
    store.append(_prices("X", "2026-01-01", [1.0 + 0.01 * i for i in range(120)]))
    log_call(store, _call(LeanAction.BUY_EARLY), fetched_at="t")  # resolves
    log_call(
        store,
        Call(
            call_id="pending",
            code="X",
            as_of="2026-04-01",
            action=LeanAction.BUY_EARLY,
            confidence=0.6,
            horizon_d=10,
            state_pattern=(),
        ),
        fetched_at="t",
    )
    scored = score_calls(store, "2026-04-05")  # only the first has elapsed
    assert [s.call.call_id for s in scored] == ["c-buy_early"]
