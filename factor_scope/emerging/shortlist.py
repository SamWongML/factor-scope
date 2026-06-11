"""Emerging funnel — Stage 3: re-rank the finalists to a defensible top 3.

The cascade's last narrowing (ROADMAP §8): the deterministic Stage-B scorecard ranks a theme's
candidate funds to a handful of finalists cheaply, and only *those few* earn a **cheap-LLM read** of
their qualitative materials. Spending the expensive model on the whole universe is the waste the
funnel exists to avoid; spending it on the finalists is the point.

The re-rank keeps the Stage-B total as its **spine** — funds that separate on the disciplined
scorecard are never reordered, so judgment never collapses into a fitted composite. The cheap-LLM
read only decides a **near-tie** (totals within :data:`TIE_BAND`). On top of the ordering, two
business rules the score does not encode prune the list:

* **de-dup leveraged repeats** — two finalists that re-buy the *same* core names (already held
  through my book) double one redundant bet; keep the better, drop the repeat (diversity).
* **freshness** — a finalist whose read is stale relative to the run date is dropped, not chased.

Online the read is :class:`LLMReranker` (DeepSeek V4 Flash); the offline test mode swaps in the
deterministic :class:`FakeReranker`, so the suite stays byte-for-byte without a network or keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from factor_scope.config import ModelSpec
from factor_scope.emerging.stage_b import FundScore

if TYPE_CHECKING:
    from factor_scope.config import Config

__all__ = [
    "FRESHNESS_WINDOW_DAYS",
    "NEAR_MISS_N",
    "TIE_BAND",
    "TOP_N",
    "FakeReranker",
    "LLMReranker",
    "RankedFund",
    "Reranker",
    "get_reranker",
    "rerank",
]

# Economic-meaning constants (never tuned to P&L):
FRESHNESS_WINDOW_DAYS = 120  # a finalist read older than this vs the run date is stale → dropped
TIE_BAND = 0.02  # Stage-B totals within this are a near-tie the cheap-LLM read decides
TOP_N = 3  # only three finalists reach the bull/bear seats
NEAR_MISS_N = 2  # finalists just below the cut, surfaced to the seats as veto-only context

# The default cheap re-rank tier — DeepSeek V4 Flash, an explicit V4 id (never the deprecating
# ``deepseek-chat`` / ``deepseek-reasoner`` aliases). Swap to ``-pro`` by editing this one line.
RERANK_MODEL = ModelSpec("deepseek:deepseek-v4-flash")


@dataclass(frozen=True)
class RankedFund:
    """A finalist's Stage-B score and its final rank after the re-rank narrows to the top 3."""

    score: FundScore
    rank: int


@runtime_checkable
class Reranker(Protocol):
    """The cheap-LLM seam: read one finalist's qualitative materials into a preference ∈ [0,1]."""

    def read(self, theme: str, score: FundScore) -> float: ...


class FakeReranker:
    """The offline stand-in: a deterministic pure-play read, no network and no keys.

    The cheap-LLM's job is a qualitative pure-play conviction read; offline that is mirrored by the
    fund's *measured* pure-play (the mapping-derived methodology), so a cleaner pure-play wins a
    near-tie. Deterministic given a snapshot, so the suite stays byte-for-byte.
    """

    def read(self, theme: str, score: FundScore) -> float:
        return max(0.0, min(1.0, score.candidate.methodology))


class LLMReranker:
    """The production read — a cheap DeepSeek V4 pass over one finalist's qualitative materials.

    A single ``ModelSpec`` swapped on its own line: a provider-prefixed id, or a ``base_url`` +
    api-key env for any OpenAI-compatible endpoint. ``pydantic_ai`` is imported inside the call (the
    ``discovery`` extra), so the offline test mode never loads it. No fallback chain, no per-model
    code — the deterministic fake covers the offline path.
    """

    def __init__(self, model: ModelSpec) -> None:
        self._model = model

    def read(self, theme: str, score: FundScore) -> float:  # pragma: no cover - host-only deps
        import os

        from pydantic import BaseModel, ConfigDict
        from pydantic_ai import Agent

        class _Read(BaseModel):
            model_config = ConfigDict(frozen=True)

            preference: float  # ∈ [0,1]: a cleaner, more convincing pure-play scores higher

        if self._model.base_url is not None:
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            api_key = os.environ[self._model.api_key_env] if self._model.api_key_env else None
            model: object = OpenAIChatModel(
                self._model.model,
                provider=OpenAIProvider(base_url=self._model.base_url, api_key=api_key),
            )
        else:
            model = self._model.model
        agent = Agent(model, output_type=_Read, instructions=_RERANK_INSTRUCTIONS)
        c = score.candidate
        seed = f"Theme: {theme}\nFund: {c.name} ({c.code})\nMeasured pure-play: {c.methodology:.2f}"
        read: _Read = agent.run_sync(seed).output
        return max(0.0, min(1.0, read.preference))


# The cheap read judges only qualitative conviction — the disciplined scorecard already ranked it.
_RERANK_INSTRUCTIONS = (
    "You are the cheap final pass over a shortlisted A-share thematic fund. The deterministic "
    "scorecard has already ranked it; you only judge its qualitative pure-play conviction for the "
    "theme. Return a single preference in [0,1] — higher means a cleaner, more convincing "
    "pure-play. Do not restate the scorecard numbers; never invent facts."
)


def get_reranker(config: Config) -> Reranker:
    """The production DeepSeek read by default; the deterministic fake in the offline test mode."""

    if config.source == "fixtures":
        return FakeReranker()
    return LLMReranker(RERANK_MODEL)  # pragma: no cover - production engine, host-only deps


def _fresh(candidate_as_of: str, run_as_of: str) -> bool:
    """A finalist is fresh when its read is within the window of the run date (else stale)."""

    age = (date.fromisoformat(run_as_of) - date.fromisoformat(candidate_as_of)).days
    return age <= FRESHNESS_WINDOW_DAYS


def _redundant(score: FundScore, kept: list[FundScore]) -> bool:
    """A leveraged repeat: this fund's core-overlap names are all already covered by a kept fund."""

    names = set(score.overlap_names)
    return bool(names) and any(names <= set(k.overlap_names) for k in kept)


def rerank(
    reranker: Reranker,
    theme: str,
    finalists: Sequence[FundScore],
    run_as_of: str,
    *,
    top_n: int = TOP_N,
    near_n: int = 0,
) -> list[RankedFund]:
    """Re-rank Stage-B finalists to the top ``n`` with the cheap-LLM read + business rules.

    The Stage-B total is the spine: finalists are bucketed onto a :data:`TIE_BAND`-wide grid by
    total, so funds that separate on the disciplined score keep their order and only same-bucket
    near-ties are reordered by the cheap-LLM ``preference``. Stale reads are dropped (freshness) and
    leveraged repeats of an already-kept fund are dropped (de-dup), then the survivors are ranked
    ``1..n``. ``near_n`` keeps that many extra survivors below the cut (ranked on, ``n+1…``) as
    veto-only near-misses for the seats. Deterministic given a snapshot — the read needs no network.
    """

    preference = {s.candidate.code: reranker.read(theme, s) for s in finalists}
    fresh = [s for s in finalists if _fresh(s.candidate.as_of, run_as_of)]
    # Spine = Stage-B total bucketed to the tie band; the cheap-LLM read breaks a near-tie; code
    # is the final deterministic tie-break.
    fresh.sort(
        key=lambda s: (
            -round(s.total / TIE_BAND),
            -preference[s.candidate.code],
            s.candidate.code,
        )
    )
    kept: list[FundScore] = []
    for score in fresh:
        if _redundant(score, kept):
            continue
        kept.append(score)
        if len(kept) == top_n + near_n:
            break
    return [
        RankedFund(score=s, rank=rank)
        for rank, s in enumerate(kept, start=1)
    ]
