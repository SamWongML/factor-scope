"""Digestion — bull/bear debate → a synthesis seat → a calibrated lean.

The public surface is the provider interface (:class:`LLMProvider` + its structured
:class:`DigestInput` / :class:`Case` / :class:`Proposal`), the
:class:`~factor_scope.digest.fake.FakeProvider` that backs the offline test default, and the
:func:`digest_item` orchestrator that runs the debate and enforces the hard guardrails (gate,
abstain-when-blind, scorecard). The real ``claude_code`` provider is the online default; CI forces
offline, so only the fake runs there.
"""

from __future__ import annotations

from factor_scope.digest.orchestrator import (
    DEFAULT_HORIZON_D,
    Debate,
    DebateCache,
    DigestResult,
    SeatBudget,
    digest_item,
    digest_key,
    state_tokens,
)
from factor_scope.digest.provider import (
    Case,
    DigestInput,
    LLMProvider,
    Proposal,
    Side,
    get_provider,
)

__all__ = [
    "DEFAULT_HORIZON_D",
    "Case",
    "Debate",
    "DebateCache",
    "DigestInput",
    "DigestResult",
    "LLMProvider",
    "Proposal",
    "SeatBudget",
    "Side",
    "digest_item",
    "digest_key",
    "get_provider",
    "state_tokens",
]
