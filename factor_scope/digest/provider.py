"""The digestion provider interface.

Digestion turns the structured inputs — factor states, the gate, connections, the scorecard — into
a calibrated lean via a two-sided bull/bear debate then a synthesis seat. A *provider* supplies the
judgment: it argues each side from the same facts (consider-the-opposite, isolated contexts) and
synthesises a proposal. The hard guardrails (gate, abstain-when-blind, scorecard) live in the
:mod:`~factor_scope.digest.orchestrator`, not the provider, so even a misbehaving real model can
never open the gate. The default provider is a deterministic **fake**; real providers are opt-in.
"""

from __future__ import annotations

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
    inputs the evidence-quality auto-downgrade reasons over (spec §08). All of it is descriptive.
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
    orchestrator from the *final* (post-guardrail) action, so they always match the lean shipped.
    """

    action: LeanAction
    confidence: float
    rationale: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class LLMProvider(Protocol):
    """The judgment surface the orchestrator drives. Real impls drop in behind this shape."""

    @property
    def name(self) -> str: ...

    def argue(self, side: Side, brief: DigestInput) -> Case: ...

    def synthesize(self, brief: DigestInput, bull: Case, bear: Case) -> Proposal: ...


def get_provider(name: str) -> LLMProvider:
    """Select a judgment provider by name. ``fake`` (default) is the only one CI ever calls.

    DeepSeek is a *chore* model (reformat/summarise, off the judgment path), so it is not
    a judgment provider — selecting it is an error pointing the user at the real options.
    """

    if name == "fake":
        from factor_scope.digest.fake import FakeProvider

        return FakeProvider()
    if name == "claude_code":
        from factor_scope.digest.claude_code import ClaudeCodeProvider

        return ClaudeCodeProvider()
    if name == "deepseek":
        raise ValueError(
            "deepseek is a chore model (off the judgment path); judgment stays on a real "
            "provider — use --provider fake (default) or claude_code."
        )
    raise ValueError(f"unknown LLM provider: {name!r} (expected: fake | claude_code)")
