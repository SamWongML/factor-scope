"""A seat that raises degrades the item to abstain — it never crashes the run.

The "invalid inputs degrade, never raise" invariant applies to the real LLM path too: a missing
`claude` binary, a timeout, a non-zero exit, or malformed JSON must degrade *that item* to an
explicit abstain (mirroring ``FactorState(valid=False)`` and abstain-when-blind), carrying the error
for the ops run log — never propagate and abort the night. These tests drive that through the public
``digest_item`` orchestrator and the real ``ClaudeCodeProvider`` (its subprocess turn stubbed).
"""

from __future__ import annotations

import subprocess

import pytest

from factor_scope.contract import Band, FactorState, GateState, LeanAction, ListName
from factor_scope.digest import Case, DigestInput, Proposal, Side, digest_item
from factor_scope.digest.claude_code import ClaudeCodeProvider

pytestmark = pytest.mark.unit


def _seeing_brief() -> DigestInput:
    """A brief that clears abstain-when-blind (known gate + 2 valid states), so the debate runs."""

    return DigestInput(
        code="159915",
        name="ChiNext ETF",
        list_name=ListName.HOLDINGS,
        states=(
            FactorState(factor="reversal", level=Band.HIGH, direction="stretched", valid=True),
            FactorState(factor="macro dial", level=Band.HIGH, direction="tight", valid=True),
        ),
        gate=GateState.OPEN,
    )


class _ArgueRaises:
    """A provider whose first seat blows up (a transient failure on the bull seat)."""

    name = "boom"

    def argue(self, side: Side, brief: DigestInput) -> Case:
        raise RuntimeError("seat boom")

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        raise AssertionError("synthesis must not run once a seat has failed")


class _SynthesizeRaises:
    """Both seats argue, but the synthesis call fails (malformed JSON from the synthesis seat)."""

    name = "boom"

    def argue(self, side: Side, brief: DigestInput) -> Case:
        return Case(side=side, strength=0.0, confidence=0.5)

    def seats(self, brief: DigestInput) -> tuple[Case, Case]:
        return self.argue(Side.BULL, brief), self.argue(Side.BEAR, brief)

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal:
        raise ValueError("synthesis boom")


def test_a_failing_seat_degrades_to_abstain_with_the_error_never_raises() -> None:
    result = digest_item(_ArgueRaises(), _seeing_brief())

    assert result.action is LeanAction.ABSTAIN
    assert result.confidence == 0.0
    assert result.error is not None and "seat boom" in result.error


def test_a_failing_synthesis_degrades_to_abstain_with_the_error() -> None:
    result = digest_item(_SynthesizeRaises(), _seeing_brief())

    assert result.action is LeanAction.ABSTAIN
    assert result.error is not None and "synthesis boom" in result.error


def _returns(stdout: str):
    """A ``subprocess.run`` stub that returns a completed turn carrying ``stdout``."""

    def run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    return run


def _raises(exc: BaseException):
    """A ``subprocess.run`` stub that fails the headless turn with ``exc``."""

    def run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise exc

    return run


# The four failure modes called out in the gap analysis, plus a non-object JSON body.
_FAILURE_MODES = [
    pytest.param(_raises(FileNotFoundError("no such file: 'claude'")), "FileNotFoundError",
                 id="missing-binary"),
    pytest.param(_raises(subprocess.CalledProcessError(1, ["claude"])), "CalledProcessError",
                 id="non-zero-exit"),
    pytest.param(_raises(subprocess.TimeoutExpired(["claude"], 120.0)), "TimeoutExpired",
                 id="timeout"),
    pytest.param(_returns("this is not json"), "JSONDecodeError", id="malformed-json"),
    pytest.param(_returns("[1, 2, 3]"), "ValueError", id="non-object-json"),
]


@pytest.mark.parametrize("fake_run, error_kind", _FAILURE_MODES)
def test_claude_code_failure_modes_degrade_to_abstain(
    monkeypatch: pytest.MonkeyPatch, fake_run, error_kind: str
) -> None:
    # The real provider, with only its headless `claude -p` turn stubbed to fail.
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = digest_item(ClaudeCodeProvider(), _seeing_brief())

    assert result.action is LeanAction.ABSTAIN
    assert result.error is not None and error_kind in result.error
    assert result.quota_deferred is False  # an ordinary crash is not a quota exhaustion


def test_a_plan_quota_exhaustion_is_flagged_quota_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider's rolling usage window is spent: `claude -p` exits non-zero with a usage-limit
    # message. Exit codes don't distinguish quota from a crash, so it's detected from the message
    # and flagged quota_deferred — the item is a deferred non-decision (degraded, error-carrying),
    # and the caller will circuit-break the rest of the run rather than retry into a closed window.
    err = subprocess.CalledProcessError(
        1, ["claude"], stderr="Claude usage limit reached · resets 3am (UTC)"
    )
    monkeypatch.setattr(subprocess, "run", _raises(err))

    result = digest_item(ClaudeCodeProvider(), _seeing_brief())

    assert result.action is LeanAction.ABSTAIN
    assert result.quota_deferred is True
    assert result.error is not None and "quota" in result.error.lower()


def test_a_generic_nonzero_exit_is_not_misread_as_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    # A crash with no usage-limit signal must NOT be misclassified as a quota exhaustion: it stays
    # an ordinary degrade (no circuit breaker), so one flaky call never silently defers the run.
    err = subprocess.CalledProcessError(1, ["claude"], stderr="panic: segmentation fault")
    monkeypatch.setattr(subprocess, "run", _raises(err))

    result = digest_item(ClaudeCodeProvider(), _seeing_brief())

    assert result.quota_deferred is False
    assert result.error is not None and "CalledProcessError" in result.error
