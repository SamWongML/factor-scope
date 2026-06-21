"""The per-host circuit breaker that caps how hard a blocked source is re-hit within one run.

The EastMoney K-line host (``push2his``) blocks the IP under a sustained full-universe burst and
stays blocked for a long, undocumented cooldown. Once it starts refusing there is no point spending
three retries × the wall-clock deadline on every remaining fund: after a few consecutive failures
the breaker *opens* and the price/activity adapters route straight to their fallback (Sina / spot
board) for the rest of the run. It is run-scoped — reset at the top of each market gather.
"""

from __future__ import annotations

import logging

import pytest

from factor_scope import ingest
from factor_scope.ingest.base import EASTMONEY_KLINE, _HostBreaker, host_breaker

pytestmark = pytest.mark.unit


def test_breaker_opens_after_threshold_consecutive_failures() -> None:
    breaker = _HostBreaker(threshold=3)
    assert not breaker.is_open(EASTMONEY_KLINE)  # closed until the host starts refusing
    breaker.record_failure(EASTMONEY_KLINE)
    breaker.record_failure(EASTMONEY_KLINE)
    assert not breaker.is_open(EASTMONEY_KLINE)  # under the threshold → still trying the host
    breaker.record_failure(EASTMONEY_KLINE)
    assert breaker.is_open(EASTMONEY_KLINE)  # the third consecutive failure trips it open


def test_a_success_closes_the_streak() -> None:
    breaker = _HostBreaker(threshold=2)
    breaker.record_failure(EASTMONEY_KLINE)
    breaker.record_success(EASTMONEY_KLINE)  # the host answered → the streak resets
    breaker.record_failure(EASTMONEY_KLINE)
    assert not breaker.is_open(EASTMONEY_KLINE)  # only one consecutive failure since the success


def test_a_success_reopens_a_tripped_host() -> None:
    breaker = _HostBreaker(threshold=1)
    breaker.record_failure(EASTMONEY_KLINE)
    assert breaker.is_open(EASTMONEY_KLINE)
    breaker.record_success(EASTMONEY_KLINE)  # the cooldown passed mid-run → resume hitting it
    assert not breaker.is_open(EASTMONEY_KLINE)


def test_breaker_counts_total_failures_for_the_run_alarm() -> None:
    # The run-level alarm reports how many funds the host refused this run, not 450 per-call logs.
    breaker = _HostBreaker(threshold=2)
    for _ in range(4):
        breaker.record_failure(EASTMONEY_KLINE)
    assert breaker.failures(EASTMONEY_KLINE) == 4


def test_reset_clears_all_run_scoped_state() -> None:
    breaker = _HostBreaker(threshold=1)
    breaker.record_failure(EASTMONEY_KLINE)
    breaker.reset()
    assert not breaker.is_open(EASTMONEY_KLINE)
    assert breaker.failures(EASTMONEY_KLINE) == 0


def test_run_alarm_warns_once_when_the_host_blocked_the_run(caplog) -> None:
    # The whole point of the breaker: a blocked host becomes one run-level line, not 450 per-call
    # warnings. The summary carries the refusal count an operator scans the morning review for.
    for _ in range(7):
        host_breaker.record_failure(EASTMONEY_KLINE)
    with caplog.at_level(logging.WARNING):
        ingest._check_eastmoney_health()
    blocked = [r for r in caplog.records if "blocked this run" in r.getMessage()]
    assert len(blocked) == 1
    assert "7" in blocked[0].getMessage()  # the run's refusal count


def test_run_alarm_is_silent_when_the_host_was_healthy(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        ingest._check_eastmoney_health()  # breaker closed (conftest reset) → no alarm
    assert not [r for r in caplog.records if "blocked" in r.getMessage()]
