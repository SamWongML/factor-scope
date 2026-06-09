"""Theme assessment — the LLM-populated half of discovery, evidence-rich.

Stage A's decisive filters are judgments, not counts: does a theme have **broad adoption**, a
**path to profit**, **fad-resistance**, and **lead-chain** (overseas-relay) corroboration? A
:class:`ThemeAssessor` answers each with a boolean **and a cited :class:`Evidence`** — the dated,
sourced one-liner the user actually reads — so a populated field is never a bare claim.

Two impls behind one ``Protocol``: :class:`FakeAssessor` reads deterministic cues off the corpus
(the only impl CI runs); :class:`LLMAssessor` lazily builds a Pydantic-AI ``Agent`` whose model is a
single config string — ``deepseek:deepseek-v4-pro`` by default, any OpenAI-compatible endpoint
(Qwen / GLM / Kimi) by config, with no per-model code and no fallback chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from factor_scope.contract import Evidence
from factor_scope.discovery.topics import StreamDoc, TopicTrajectory

if TYPE_CHECKING:
    from factor_scope.config import Config

__all__ = [
    "FakeAssessor",
    "FieldVerdict",
    "LLMAssessor",
    "ThemeAssessment",
    "ThemeAssessor",
    "get_assessor",
]


class FieldVerdict(BaseModel):
    """One durability/corroboration judgment: does it hold, and the evidence that says so."""

    model_config = ConfigDict(frozen=True)

    holds: bool
    evidence: Evidence


class ThemeAssessment(BaseModel):
    """The four Stage-A judgment fields, each a :class:`FieldVerdict` carrying its citation."""

    model_config = ConfigDict(frozen=True)

    broad_adoption: FieldVerdict
    path_to_profit: FieldVerdict
    fad_resistant: FieldVerdict
    lead_chain: FieldVerdict


@runtime_checkable
class ThemeAssessor(Protocol):
    """The swappable judgment seam: a topic + its documents → the four cited verdicts."""

    def assess(self, topic: TopicTrajectory, evidence_docs: list[StreamDoc]) -> ThemeAssessment:
        """Fill the four durability/corroboration fields, each with a cited :class:`Evidence`."""


# Cue vocabularies — the economic markers each judgment reads off the corpus (never tuned to P&L).
_ADOPTION_CUES = ("装机", "出货", "放量", "渗透率")  # real-economy uptake, not a niche
_PROFIT_CUES = ("毛利", "盈利", "订单", "降本")  # a credible route to earnings
_FAD_CUES = ("概念", "题材", "炒作", "蹭热点")  # hype markers → the opposite of fad-resistant
_LEAD_CHAIN_CUES = ("特斯拉", "海外", "美国", "北美")  # the overseas relay confirms end-demand


def _cite(topic: TopicTrajectory, docs: list[StreamDoc], cues: tuple[str, ...]) -> FieldVerdict:
    """The earliest document hitting any cue is the citation; absent a hit the verdict is False."""

    for doc in sorted(docs, key=lambda d: (d.as_of, d.doc_id)):
        if any(cue in doc.text for cue in cues):
            return FieldVerdict(
                holds=True,
                evidence=Evidence(src=doc.source, as_of=doc.as_of, one_line=doc.text),
            )
    return FieldVerdict(
        holds=False,
        evidence=Evidence(
            src=f"discovery:{topic.label}",
            as_of=docs[0].as_of if docs else "",
            one_line=f"no corroborating mention for {topic.label}",
        ),
    )


class FakeAssessor:
    """Deterministic cue-reading over the corpus — the only impl CI runs.

    Each field is True iff some document carries its cue vocabulary, and the earliest such doc is
    the citation; ``fad_resistant`` inverts (True unless a hype marker is present). A stand-in for
    the LLM's judgment that needs no network and no keys, so the offline suite stays hermetic.
    """

    def assess(self, topic: TopicTrajectory, evidence_docs: list[StreamDoc]) -> ThemeAssessment:
        docs = [d for d in evidence_docs if d.doc_id in set(topic.doc_ids)] or evidence_docs
        fad = _cite(topic, docs, _FAD_CUES)
        # fad-resistance is the inverse read: resilient unless a hype marker is cited.
        fad_resistant = FieldVerdict(holds=not fad.holds, evidence=fad.evidence)
        return ThemeAssessment(
            broad_adoption=_cite(topic, docs, _ADOPTION_CUES),
            path_to_profit=_cite(topic, docs, _PROFIT_CUES),
            fad_resistant=fad_resistant,
            lead_chain=_cite(topic, docs, _LEAD_CHAIN_CUES),
        )


class LLMAssessor:
    """The real judgment — a lazily-built Pydantic-AI ``Agent`` returning a ``ThemeAssessment``.

    The model is a single config string. A built-in provider prefix (``deepseek:…``, ``openai:…``,
    ``anthropic:…``, ``moonshotai:kimi-…``) passes straight to the agent; otherwise a configured
    ``discovery_base_url`` (+ api-key env) builds an ``OpenAIChatModel`` for **any** compatible
    endpoint — so Qwen / GLM / Kimi are a config change, not code. Exactly one model: no fallback
    chain, no compatibility shim. ``pydantic_ai`` is imported inside the call (the ``discovery``
    extra), so the offline path never loads it.
    """

    def __init__(
        self, model: str, *, base_url: str | None = None, api_key_env: str | None = None
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key_env = api_key_env

    def _agent(self) -> object:  # pragma: no cover - opt-in live path
        import os

        from pydantic_ai import Agent

        instructions = (
            "You assess an emerging investment theme for an A-share funnel. From the cited "
            "materials, judge whether it has broad adoption, a credible path to profit, "
            "resilience to being a one-cycle fad, and overseas lead-chain corroboration. Populate "
            "each field with a boolean and a single dated, sourced evidence line — never invent a "
            "citation."
        )
        if self._base_url is not None:
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            api_key = os.environ[self._api_key_env] if self._api_key_env else None
            model: object = OpenAIChatModel(
                self._model, provider=OpenAIProvider(base_url=self._base_url, api_key=api_key)
            )
        else:
            model = self._model
        return Agent(model, output_type=ThemeAssessment, instructions=instructions)

    def assess(  # pragma: no cover - opt-in live path
        self, topic: TopicTrajectory, evidence_docs: list[StreamDoc]
    ) -> ThemeAssessment:
        docs = [d for d in evidence_docs if d.doc_id in set(topic.doc_ids)] or evidence_docs
        materials = "\n".join(f"[{d.source} {d.as_of}] {d.text}" for d in docs)
        seed = f"Theme: {topic.label}\nConstituents: {', '.join(topic.constituents)}"
        result = self._agent().run_sync(f"{seed}\n\n{materials}")  # type: ignore[attr-defined]
        return result.output  # type: ignore[no-any-return]


def get_assessor(config: Config) -> ThemeAssessor:
    """The deterministic fake offline, the lazy LLM assessor online (mirrors the source seams)."""

    if config.source == "fixtures":
        return FakeAssessor()
    return LLMAssessor(  # pragma: no cover - opt-in live path
        config.discovery_model,
        base_url=config.discovery_base_url,
        api_key_env=config.discovery_api_key_env,
    )
