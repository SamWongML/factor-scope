"""A digested item is logged as a falsifiable call only when the model actually decided.

A seat failure (a missing/timed-out/quota-exhausted ``claude`` call) or a budget cap degrades that
item to an abstain-*with-error* — the model never judged it, so it is a non-decision, not a no-bet.
``_attach_leans`` must keep degrading the **display** (the run still completes on a sparser
artifact) but must **not** commit a call-of-record for it: the self-scoring loop grades only the
model genuinely made. A later run on the identical brief then commits the real call as the first
write through the existing per-night idempotency, with no supersede machinery.
"""

from __future__ import annotations

import pytest

from factor_scope import pipeline
from factor_scope.contract import (
    Band,
    DashboardItem,
    DigestStatus,
    FactorState,
    GateState,
    LeanAction,
    ListName,
)
from factor_scope.digest import Case, DigestInput, Proposal, Side
from factor_scope.digest.provider import QuotaExhausted
from factor_scope.scoring import read_calls
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit

AS_OF = "2026-06-29"


def _item(code: str, name: str) -> tuple[str, DashboardItem]:
    """A seeing item (known gate + two valid states) so the debate runs, not a blind abstain."""

    return code, DashboardItem(
        item=name,
        list_name=ListName.HOLDINGS,
        gate=GateState.OPEN,
        states=[
            FactorState(factor="reversal", level=Band.HIGH, direction="stretched", valid=True),
            FactorState(factor="macro dial", level=Band.HIGH, direction="tight", valid=True),
        ],
    )


class _ProviderFailingFor:
    """A provider whose synthesis seat raises only for ``boom_code`` — degrades that item alone."""

    name = "selective"
    usage: list[object] = []

    def __init__(self, boom_code: str) -> None:
        self._boom = boom_code

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=2.0 if side is Side.BULL else 0.5, confidence=0.6)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        if brief.code == self._boom:
            raise RuntimeError("seat boom")
        return Proposal(action=LeanAction.TRIM, confidence=0.6)


def _attach(store: DuckDBStore, pairs: list[tuple[str, DashboardItem]], provider: object) -> None:
    pipeline._attach_leans(
        pairs, store, AS_OF, provider_name="selective", deep_think_model="opus", near_misses={}
    )


def test_an_errored_item_degrades_for_display_but_logs_no_call(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "get_provider", lambda *a, **k: _ProviderFailingFor("BOOM"))
    store = DuckDBStore(":memory:")
    try:
        good, boom = _item("GOOD", "Good ETF"), _item("BOOM", "Boom ETF")
        _attach(store, [good, boom], _ProviderFailingFor("BOOM"))

        # Display still degrades — both items carry a lean, the run completes on a sparser artifact.
        assert good[1].lean is not None and boom[1].lean is not None
        assert boom[1].lean.action is LeanAction.ABSTAIN  # the degraded display

        # But only the genuinely-decided item is committed as a falsifiable call.
        logged = {c.code for c in read_calls(store, AS_OF) if c.as_of == AS_OF}
        assert logged == {"GOOD"}  # the errored item is a non-decision — not a call-of-record
    finally:
        store.close()


def test_the_repair_run_commits_the_real_call_as_the_first_write(monkeypatch) -> None:
    # The night the seat failed, nothing was committed for BOOM. A later run on the identical brief
    # with a healthy provider commits the real call as the first (and only) write — the existing
    # per-night idempotency lets it through precisely because no call was logged before.
    store = DuckDBStore(":memory:")
    try:
        monkeypatch.setattr(pipeline, "get_provider", lambda *a, **k: _ProviderFailingFor("BOOM"))
        _attach(store, [_item("BOOM", "Boom ETF")], _ProviderFailingFor("BOOM"))
        assert [c.code for c in read_calls(store, AS_OF) if c.as_of == AS_OF] == []

        monkeypatch.setattr(pipeline, "get_provider", lambda *a, **k: _ProviderFailingFor("NONE"))
        boom = _item("BOOM", "Boom ETF")
        _attach(store, [boom], _ProviderFailingFor("NONE"))

        calls = [c for c in read_calls(store, AS_OF) if c.as_of == AS_OF and c.code == "BOOM"]
        assert len(calls) == 1 and calls[0].action is LeanAction.TRIM  # the real call, logged once
    finally:
        store.close()


class _QuotaProvider:
    """A provider whose seats raise QuotaExhausted — the window is spent. Counts seat calls."""

    name = "quota"
    usage: list[object] = []

    def __init__(self) -> None:
        self.seat_calls = 0

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=1.0, confidence=0.5)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        self.seat_calls += 1
        raise QuotaExhausted("3am (UTC)")

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        raise AssertionError("synthesis must not run once the seats hit the quota wall")


def test_quota_exhaustion_circuit_breaks_the_rest_of_the_run(monkeypatch) -> None:
    # Once one item hits the plan-quota wall, the remaining items must NOT each pay a failing
    # `claude -p`: the breaker opens and defers them without touching the provider. Every quota item
    # is a non-decision — none is committed, all are pending for the resume run.
    provider = _QuotaProvider()
    monkeypatch.setattr(pipeline, "get_provider", lambda *a, **k: provider)
    store = DuckDBStore(":memory:")
    try:
        pairs = [_item(c, f"{c} ETF") for c in ("A", "B", "C")]
        failures: list[object] = []
        pipeline._attach_leans(
            pairs, store, AS_OF, provider_name="quota", deep_think_model="opus",
            near_misses={}, digest_failures=failures,
        )
        assert provider.seat_calls == 1  # only the first item probed; the rest were circuit-broken
        assert [c.code for c in read_calls(store, AS_OF) if c.as_of == AS_OF] == []  # none logged
        assert {f.code for f in failures} == {"A", "B", "C"}  # all three recorded as failures
    finally:
        store.close()


def test_digest_status_marks_deferred_distinct_from_decided(monkeypatch) -> None:
    # A consumer must never read a not-yet-processed item as a model "no-bet". The errored item is
    # marked DEFERRED (pending), the genuinely-judged item DECIDED — an explicit, non-nullable
    # signal carried in the artifact, independent of the (placeholder) display lean.
    monkeypatch.setattr(pipeline, "get_provider", lambda *a, **k: _ProviderFailingFor("BOOM"))
    store = DuckDBStore(":memory:")
    try:
        good, boom = _item("GOOD", "Good ETF"), _item("BOOM", "Boom ETF")
        _attach(store, [good, boom], _ProviderFailingFor("BOOM"))
        assert good[1].digest_status is DigestStatus.DECIDED
        assert boom[1].digest_status is DigestStatus.DEFERRED
    finally:
        store.close()


def _price(code: str, as_of: str) -> Reading:
    return Reading(series="prices", key=code, as_of=as_of, fetched_at=as_of, payload={"nav": 1.0})


def test_a_call_is_sealed_once_its_forward_window_has_opened(monkeypatch) -> None:
    # A stale re-run (--as-of a past night) whose store has moved on — a price now exists *after*
    # the call's as_of — must not back-fill a new call: committing it once the forward move is
    # knowable would be hindsight. The window-open seal blocks it even with a healthy provider.
    monkeypatch.setattr(pipeline, "get_provider", lambda *a, **k: _ProviderFailingFor("NONE"))
    store = DuckDBStore(":memory:")
    try:
        store.append([_price("LATE", "2026-06-30")])  # a price dated after AS_OF (2026-06-29)
        _attach(store, [_item("LATE", "Late ETF")], _ProviderFailingFor("NONE"))
        assert [c.code for c in read_calls(store, AS_OF) if c.as_of == AS_OF] == []  # sealed out
    finally:
        store.close()


def test_a_call_commits_while_its_forward_window_is_still_open(monkeypatch) -> None:
    # The control: with no price dated after the call's as_of (the normal forward-moving nightly),
    # the window is not yet open, so a genuine call commits as usual.
    monkeypatch.setattr(pipeline, "get_provider", lambda *a, **k: _ProviderFailingFor("NONE"))
    store = DuckDBStore(":memory:")
    try:
        store.append([_price("ONTIME", "2026-06-29")])  # the entry price, dated on the call's night
        _attach(store, [_item("ONTIME", "On-time ETF")], _ProviderFailingFor("NONE"))
        assert [c.code for c in read_calls(store, AS_OF) if c.as_of == AS_OF] == ["ONTIME"]
    finally:
        store.close()
