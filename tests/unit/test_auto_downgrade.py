"""Unit tests for the evidence-quality auto-downgrade (spec §08).

§08 mandates evidence-grounded reasoning with an *auto-downgrade* on low-quality evidence:
stale, single-source, conflicting, or forum-only. The downgrade is a deterministic, pure
function of the brief — it may only *lower* the stated confidence, and it can never change the
action, a factor state, or the trend gate. These tests pin one condition at a time.
"""

import pytest

from factor_scope.contract import (
    Band,
    Evidence,
    FactorState,
    GateState,
    LeanAction,
    ListName,
)
from factor_scope.digest import DigestInput, digest_item
from factor_scope.digest.fake import FakeProvider
from factor_scope.digest.orchestrator import (
    LOW_TRUST_SRC,
    MIN_SOURCES,
    STALE_MAX_AGE_D,
    auto_downgrade,
)

pytestmark = pytest.mark.unit

AS_OF = "2026-06-06"


def _state(factor: str, level: Band, direction: str = "x", valid: bool = True) -> FactorState:
    return FactorState(factor=factor, level=level, direction=direction, valid=valid)


def _ev(src: str, as_of: str = AS_OF) -> Evidence:
    return Evidence(src=src, as_of=as_of, one_line="x")


def _brief(
    *,
    states: tuple[FactorState, ...] = (),
    evidence: tuple[Evidence, ...] = (),
    as_of: str | None = AS_OF,
) -> DigestInput:
    return DigestInput(
        code="X",
        name="X",
        list_name=ListName.HOLDINGS,
        states=states,
        gate=GateState.OPEN,
        evidence=evidence,
        as_of=as_of,
    )


# A baseline that trips none of the four conditions: two distinct, fresh, trusted sources and no
# valid states at opposing extremes. Its multiplier is exactly 1.0 (no downgrade).
def _clean_evidence() -> tuple[Evidence, ...]:
    return (_ev("akshare:fund_etf_hist"), _ev("akshare:fund_portfolio"))


def test_no_penalty_on_clean_evidence() -> None:
    brief = _brief(
        states=(_state("reversal", Band.HIGH),),
        evidence=_clean_evidence(),
    )
    assert auto_downgrade(brief) == 1.0


def test_stale_evidence_downgrades() -> None:
    # Newest evidence older than the freshness window (vs the brief's own as_of) → stale.
    fresh = _brief(evidence=(_ev("a"), _ev("b")))
    stale = _brief(evidence=(_ev("a", "2026-05-01"), _ev("b", "2026-05-01")))
    assert auto_downgrade(stale) < auto_downgrade(fresh) == 1.0


def test_freshness_boundary_is_measured_against_as_of() -> None:
    # Exactly STALE_MAX_AGE_D old is still fresh; one day older is stale (no wall clock involved).
    on_edge = "2026-05-30"  # 7 days before 2026-06-06
    just_over = "2026-05-29"  # 8 days before
    assert auto_downgrade(_brief(evidence=(_ev("a", on_edge), _ev("b", on_edge)))) == 1.0
    assert auto_downgrade(_brief(evidence=(_ev("a", just_over), _ev("b", just_over)))) < 1.0
    assert STALE_MAX_AGE_D == 7


def test_single_source_downgrades() -> None:
    one = _brief(evidence=(_ev("a"), _ev("a")))  # one distinct source
    assert MIN_SOURCES == 2
    assert auto_downgrade(one) < 1.0
    assert auto_downgrade(_brief(evidence=())) < 1.0  # no evidence is single-source too


def test_conflict_downgrades() -> None:
    # Two valid factor states at opposing extremes — a softer downgrade below the abstain bar.
    conflict = _brief(
        states=(
            _state("reversal", Band.EXTREME_HIGH),
            _state("macro dial", Band.EXTREME_LOW),
        ),
        evidence=_clean_evidence(),
    )
    assert auto_downgrade(conflict) < 1.0


def test_invalid_states_do_not_count_as_conflict() -> None:
    no_conflict = _brief(
        states=(
            _state("reversal", Band.EXTREME_HIGH),
            _state("macro dial", Band.EXTREME_LOW, valid=False),
        ),
        evidence=_clean_evidence(),
    )
    assert auto_downgrade(no_conflict) == 1.0


def test_forum_only_downgrades() -> None:
    forum_src = next(iter(LOW_TRUST_SRC))
    forum_only = _brief(evidence=(_ev(forum_src), _ev(forum_src, "2026-06-05")))
    mixed = _brief(evidence=(_ev(forum_src), _ev("akshare:fund_etf_hist")))
    assert auto_downgrade(forum_only) < auto_downgrade(mixed)


def test_conditions_compound() -> None:
    # Stale, single-source, and forum-only together multiply below any one of them alone.
    forum_src = next(iter(LOW_TRUST_SRC))
    weak = _brief(evidence=(_ev(forum_src, "2026-01-01"),))
    one_only = _brief(evidence=(_ev("a"), _ev("a")))  # single-source only
    assert 0.0 < auto_downgrade(weak) < auto_downgrade(one_only) < 1.0


def test_downgrade_never_raises() -> None:
    for brief in (
        _brief(),
        _brief(evidence=_clean_evidence()),
        _brief(states=(_state("reversal", Band.EXTREME_HIGH),), evidence=_clean_evidence()),
    ):
        assert 0.0 < auto_downgrade(brief) <= 1.0


def test_downgrade_is_deterministic() -> None:
    brief = _brief(evidence=(_ev("xueqiu", "2026-01-01"),))
    assert auto_downgrade(brief) == auto_downgrade(brief)


# --- Integration through the orchestrator: confidence only, action untouched -------------------


def _bearish(evidence: tuple[Evidence, ...]) -> DigestInput:
    # A holding stretched up under a tight macro dial → Trim (a real, non-abstain lean).
    return DigestInput(
        code="X",
        name="X",
        list_name=ListName.HOLDINGS,
        states=(
            _state("reversal", Band.HIGH, "ran up → reversal-DOWN risk"),
            _state("trend gate", Band.HIGH, "uptrend"),
            _state("macro dial", Band.HIGH, "tight"),
        ),
        gate=GateState.OPEN,
        evidence=evidence,
        as_of=AS_OF,
    )


def test_weak_evidence_lowers_confidence_but_not_action() -> None:
    strong = digest_item(FakeProvider(), _bearish(_clean_evidence()))
    weak = digest_item(FakeProvider(), _bearish((_ev("xueqiu", "2026-01-01"),)))
    assert strong.action is weak.action is LeanAction.TRIM  # action untouched
    assert weak.confidence < strong.confidence
    assert weak.confidence > 0.0
