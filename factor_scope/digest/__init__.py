"""Digestion — bull/bear debate → a synthesis seat → a calibrated lean.

The public surface is the provider interface (:class:`LLMProvider` + its structured
:class:`DigestInput` / :class:`Case` / :class:`Proposal`), the deterministic default
:class:`~factor_scope.digest.fake.FakeProvider`, and the :func:`digest_item` orchestrator that runs
the debate and enforces the hard guardrails (gate, abstain-when-blind, scorecard). Real providers
(``claude_code``) are opt-in behind :func:`get_provider` and never called in CI.
"""

from __future__ import annotations

from factor_scope.digest.orchestrator import (
    DEFAULT_HORIZON_D,
    DigestResult,
    digest_item,
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
    "DigestInput",
    "DigestResult",
    "LLMProvider",
    "Proposal",
    "Side",
    "digest_item",
    "get_provider",
    "state_tokens",
]
