"""The digestion provider interface.

Digestion turns the structured inputs — factor states, the gate, connections, the scorecard — into
a calibrated lean via a two-sided bull/bear debate then a synthesis seat. A *provider* supplies the
judgment: it argues each side from the same facts (consider-the-opposite, isolated contexts) and
synthesises a proposal. The hard guardrails (gate, abstain-when-blind, scorecard) live in the
:mod:`~factor_scope.digest.orchestrator`, not the provider, so even a misbehaving real model can
never open the gate. Online by default the provider is the real ``claude_code`` model; the
deterministic **fake** is the offline test default, so CI needs no network or keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from factor_scope.contract import (
    Connection,
    Evidence,
    FactorState,
    GateState,
    LeanAction,
    ListName,
    Scorecard,
)
from factor_scope.cost import Usage


class Side(StrEnum):
    """The two seats of the debate (argue both sides, isolated)."""

    BULL = "bull"
    BEAR = "bear"


@dataclass(frozen=True)
class DigestInput:
    """Everything the digest reads about one item — point-in-time, fetched not recalled.

    The scorecard is the book-wide self-scoring mirror; ``prior_action`` is the most recent prior
    lean on this code (used only to phrase the evolution line). ``evidence`` is the dated, sourced
    reads behind the item and ``as_of`` is the point-in-time date they are judged against — the
    inputs the evidence-quality auto-downgrade reasons over. ``near_misses`` are one-line summaries
    of the funnel finalists just below the cut — veto-only context for the seats, never promotable.
    All of it is descriptive.
    """

    code: str
    name: str
    list_name: ListName
    states: tuple[FactorState, ...]
    gate: GateState
    connections: tuple[Connection, ...] = ()
    connections_flag: bool = False
    gain: float | None = None
    scorecard: Scorecard | None = None
    prior_action: LeanAction | None = None
    evidence: tuple[Evidence, ...] = ()
    as_of: str | None = None
    near_misses: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    """One side's argument: how strong its case is, how sure, and the reads behind it."""

    side: Side
    strength: float  # ≥ 0 — the summed magnitude of this side's supporting reads
    confidence: float
    points: tuple[str, ...] = ()


@dataclass(frozen=True)
class Proposal:
    """The synthesis seat's raw judgment, before the orchestrator's hard guardrails.

    Deliberately just the call + conviction (+ why): the descriptive fields the artifact carries —
    text, evolution, flip-trigger, invalidation — are rendered deterministically by the
    orchestrator from the *final* (post-guardrail) action, so they always match the emitted lean.
    """

    action: LeanAction
    confidence: float
    rationale: tuple[str, ...] = field(default_factory=tuple)
    # The synthesis seat's self-scored rubric: (criterion, score) pairs. Descriptive only — it
    # informs the lean's confidence, never the orchestrator's hard guardrails. Empty when unscored.
    rubric: tuple[tuple[str, float], ...] = field(default_factory=tuple)


@runtime_checkable
class LLMProvider(Protocol):
    """The judgment surface the orchestrator drives. Real impls drop in behind this shape."""

    @property
    def name(self) -> str: ...

    def argue(self, side: Side, brief: DigestInput) -> Case: ...

    # Both seats from one brief, returned in fixed (bull, bear) slots — the real provider runs the
    # two argue calls concurrently; the orchestrator drives the debate through this, not argue().
    def seats(self, brief: DigestInput) -> tuple[Case, Case]: ...

    def synthesize(
        self, brief: DigestInput, bull: Case, bear: Case, *, present_bear_first: bool = False
    ) -> Proposal: ...

    @property
    def usage(self) -> Sequence[Usage]:
        """Per-call cost records accumulated in call order — the constant telemetry contract.

        The real providers append one :class:`~factor_scope.cost.Usage` per seat turn; the
        deterministic fake meters nothing (empty), so the offline run log + ledger stay stable.
        """
        ...


def get_provider(name: str, *, deep_think_model: str | None = None) -> LLMProvider:
    """Select a judgment provider by name. ``fake`` (default) is the only one CI ever calls.

    ``deep_think_model`` is the reserved deep-think tier the ``claude_code`` seats run on (the fake
    ignores it). DeepSeek is a *chore* model (reformat/summarise, off the judgment path), so it is
    not a judgment provider — selecting it is an error pointing the user at the real options.
    """

    if name == "fake":
        from factor_scope.digest.fake import FakeProvider

        return FakeProvider()
    if name == "claude_code":
        from factor_scope.digest.claude_code import ClaudeCodeProvider

        return ClaudeCodeProvider(model=deep_think_model)
    if name == "deepseek":
        raise ValueError(
            "deepseek is a chore model (off the judgment path); judgment stays on a real "
            "provider — use --provider fake (default) or claude_code."
        )
    raise ValueError(f"unknown LLM provider: {name!r} (expected: fake | claude_code)")
