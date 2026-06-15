"""Cross-night debate reuse — the seats are the nightly cost, so don't re-argue an unchanged brief.

The expensive bull/bear→synthesis is cached by a content hash of the *decision-relevant* brief
(states, gate, evidence, connections, near-misses — never the run date), so an unchanged item on a
new night reuses last night's provider judgment. The hard guardrails are **not** cached: the gate,
the evidence-quality downgrade and the scorecard re-run every night on the reused judgment, since
they can move (staleness bites against the new date) even when the brief did not.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from factor_scope.contract import (
    Band,
    Connection,
    Evidence,
    FactorState,
    GateState,
    LeanAction,
    ListName,
)
from factor_scope.cost import BudgetGuard, Usage
from factor_scope.digest import Case, Debate, DigestInput, Proposal, SeatBudget, Side, digest_item
from factor_scope.digest.orchestrator import digest_key

pytestmark = pytest.mark.unit


def _brief(
    *,
    as_of: str = "2026-06-05",
    gate: GateState = GateState.OPEN,
    level: Band = Band.HIGH,
    evidence: tuple[Evidence, ...] = (),
) -> DigestInput:
    return DigestInput(
        code="X",
        name="X",
        list_name=ListName.WATCHLIST,
        states=(
            FactorState(factor="reversal", level=level, direction="stretched"),
            FactorState(factor="macro dial", level=Band.HIGH, direction="tight"),
        ),
        gate=gate,
        evidence=evidence,
        as_of=as_of,
    )


class _CountingProvider:
    """A provider that counts how often the expensive seats are argued, so reuse is observable."""

    name = "counting"

    def __init__(self, *, action: LeanAction = LeanAction.HOLD, confidence: float = 0.8) -> None:
        self._action = action
        self._confidence = confidence
        self.seat_calls = 0

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=2.0 if side is Side.BULL else 0.5, confidence=0.6)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        self.seat_calls += 1
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        return Proposal(action=self._action, confidence=self._confidence)


class _MemCache:
    """In-memory debate cache keyed by the production content hash — models the store-backed one."""

    def __init__(self) -> None:
        self.entries: dict[str, Debate] = {}

    def get(self, brief: DigestInput) -> Debate | None:
        return self.entries.get(digest_key(brief))

    def put(self, brief: DigestInput, debate: Debate) -> None:
        self.entries[digest_key(brief)] = debate


def test_same_brief_reuses_the_cached_debate() -> None:
    provider = _CountingProvider()
    cache = _MemCache()
    first = digest_item(provider, _brief(), cache=cache)
    second = digest_item(provider, _brief(), cache=cache)
    assert provider.seat_calls == 1  # the second night reused the cached debate
    assert (first.action, first.confidence) == (second.action, second.confidence)


def test_a_moved_brief_re_argues_the_seats() -> None:
    # A decision-relevant change (a factor state moved band) is a different key → argue afresh.
    provider = _CountingProvider()
    cache = _MemCache()
    digest_item(provider, _brief(level=Band.HIGH), cache=cache)
    digest_item(provider, _brief(level=Band.LOW), cache=cache)
    assert provider.seat_calls == 2  # the moved state re-argued, it did not reuse


def test_a_reordered_overlap_set_is_the_same_cache_key() -> None:
    # The look-through overlaps are an unordered set: the same two names flagged in either order are
    # the same decision-relevant brief, so they must hash to one key and reuse the single debate.
    overlaps = (Connection(shared="中际旭创 ↓"), Connection(shared="沪电股份 ↓"))
    forward = replace(_brief(), connections=overlaps, connections_flag=True)
    reverse = replace(_brief(), connections=overlaps[::-1], connections_flag=True)
    assert digest_key(forward) == digest_key(reverse)


def test_an_unchanged_item_reuses_across_a_new_run_date() -> None:
    # The run date is *not* part of the key — the whole point of the dedup is that the same item on
    # a later night reuses last night's debate rather than re-paying for the seats.
    provider = _CountingProvider()
    cache = _MemCache()
    digest_item(provider, _brief(as_of="2026-06-05"), cache=cache)
    digest_item(provider, _brief(as_of="2026-09-30"), cache=cache)
    assert provider.seat_calls == 1  # months later, same brief → reused


def test_a_reused_debate_still_re_bites_on_a_later_staleness() -> None:
    # The hard guardrails re-run on the reused debate: the same two-source reads are fresh on the
    # night they land but stale weeks later, so the later night's confidence is trimmed even though
    # the expensive debate was argued only once.
    provider = _CountingProvider()
    cache = _MemCache()
    evidence = (
        Evidence(src="research", as_of="2026-06-05", one_line="order book solid"),
        Evidence(src="filing", as_of="2026-06-05", one_line="capex guidance raised"),
    )
    fresh = digest_item(provider, _brief(as_of="2026-06-06", evidence=evidence), cache=cache)
    stale = digest_item(provider, _brief(as_of="2026-06-30", evidence=evidence), cache=cache)
    assert provider.seat_calls == 1  # same evidence content → one debate, reused
    assert stale.confidence < fresh.confidence  # the later run-date made the reads stale


def test_a_zero_seat_budget_abstains_without_arguing() -> None:
    # The seat budget is a ceiling on the *fresh* seats — the nightly cost. With none left, a new
    # item abstains-with-error without arguing, the cap living outside the model like the gate.
    provider = _CountingProvider()
    result = digest_item(provider, _brief(), budget=SeatBudget(0))
    assert provider.seat_calls == 0  # never paid for the seats
    assert result.action is LeanAction.ABSTAIN
    assert result.error is not None and "seat budget" in result.error


def test_a_cached_item_does_not_spend_the_seat_budget() -> None:
    # A reused debate costs no seats, so it must not consume the budget — otherwise the cap would
    # needlessly abstain cheap cached items on the busy nights the cache is meant to cover.
    provider = _CountingProvider()
    cache = _MemCache()
    # Warm the cache with one real debate, then re-run the same brief with a spent budget.
    digest_item(provider, _brief(), cache=cache, budget=SeatBudget(5))
    result = digest_item(provider, _brief(), cache=cache, budget=SeatBudget(0))
    assert provider.seat_calls == 1  # the cache hit needed no fresh seat call
    assert result.error is None and result.action is LeanAction.HOLD


def test_a_blind_item_does_not_spend_the_seat_budget() -> None:
    # A blind item abstains before it ever reaches the seats, so it spends no budget either — and
    # the abstain it yields carries no error, unlike the budget cap's.
    provider = _CountingProvider()
    result = digest_item(provider, _brief(gate=GateState.UNKNOWN), budget=SeatBudget(0))
    assert provider.seat_calls == 0
    assert result.action is LeanAction.ABSTAIN
    assert result.error is None  # a blind abstain, not a budget abstain


class _PricyProvider(_CountingProvider):
    """A counting provider that also books a USD cost on each fresh debate (for the spend guard)."""

    usage: list[Usage]

    def __init__(self, *, cost_per_debate: float = 1.0, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._cost = cost_per_debate
        self.usage = []

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        self.usage.append(Usage("claude_code", "opus", 100, 40, self._cost))
        return super().seats(brief)


def test_an_exhausted_monthly_budget_abstains_without_arguing() -> None:
    # The USD guard, seeded at its ceiling, refuses a fresh debate before it spends — the overflow
    # degrades to abstain-with-error, the cap outside the model like the trend gate.
    provider = _PricyProvider()
    result = digest_item(provider, _brief(), spend=BudgetGuard(limit_usd=5.0, spent_usd=5.0))
    assert provider.seat_calls == 0  # never paid for the seats
    assert result.action is LeanAction.ABSTAIN
    assert result.error is not None and "monthly budget" in result.error


def test_a_fresh_debate_charges_its_realised_cost_to_the_budget() -> None:
    # A debate within budget argues and books its realised USD against the month's running total.
    provider = _PricyProvider(cost_per_debate=2.0)
    guard = BudgetGuard(limit_usd=10.0, spent_usd=0.0)
    result = digest_item(provider, _brief(), spend=guard)
    assert provider.seat_calls == 1
    assert result.error is None
    assert guard.spent_usd == pytest.approx(2.0)  # the one seats() call's cost was charged


def test_a_cached_item_does_not_spend_the_monthly_budget() -> None:
    # A reused debate spends no USD, so the guard is untouched on a cache hit.
    provider = _PricyProvider(cost_per_debate=3.0)
    cache = _MemCache()
    guard = BudgetGuard(limit_usd=10.0, spent_usd=0.0)
    digest_item(provider, _brief(), cache=cache, spend=guard)
    digest_item(provider, _brief(), cache=cache, spend=guard)
    assert provider.seat_calls == 1  # second was a cache hit
    assert guard.spent_usd == pytest.approx(3.0)  # charged once, not twice
