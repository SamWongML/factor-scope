"""Theme assessment — the LLM-populated half of discovery, evidence-rich.

Stage A's decisive filters are judgments, not counts: does a theme have **broad adoption**, a
**path to profit**, **fad-resistance**, and **lead-chain** (overseas-relay) corroboration? A
:class:`ThemeAssessor` answers each with a boolean **and a cited :class:`Evidence`** — the dated,
sourced one-liner the user actually reads — so a populated field is never a bare claim.

The production judgment is :class:`LLMAssessor` — two Pydantic-AI agents stratified by task
difficulty so cost follows the work: a cheap draft model digests the bulky raw materials, a strong
judge model renders the verdict. :class:`FakeAssessor` is the deterministic cue-reader the offline
test mode swaps in — no network, no keys — so the suite stays hermetic. One ``Protocol`` over both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from factor_scope.config import DISCOVERY_DRAFT, DISCOVERY_JUDGE, ModelSpec
from factor_scope.contract import Evidence
from factor_scope.discovery.topics import StreamDoc, TopicTrajectory

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    """The offline test stand-in: deterministic cue-reading the suite swaps in for the LLM.

    Each field is True iff some document carries its cue vocabulary, and the earliest such doc is
    the citation; ``fad_resistant`` inverts (True unless a hype marker is present). It needs no
    network and no keys, so the offline suite stays hermetic and byte-for-byte deterministic.
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


# The cheap first pass: extract, do not judge — so the bulky text-reduction runs on the cheap tier.
_DRAFT_INSTRUCTIONS = (
    "You are the cheap first pass over an emerging A-share theme's source materials. Do not "
    "judge — only extract. For each of the four dimensions — broad adoption, path to profit, "
    "fad-resistance, overseas lead-chain — quote the single most relevant dated, sourced line "
    "from the materials, or write 'none' if it is absent. Keep it terse; never invent a citation."
)
# The decisive call: only the small, hard reasoning over the brief runs on the strong tier.
_JUDGE_INSTRUCTIONS = (
    "You assess an emerging A-share theme from a pre-extracted evidence brief. Judge whether it "
    "has broad adoption, a credible path to profit, resilience to being a one-cycle fad, and "
    "overseas lead-chain corroboration. Populate each field with a boolean and a single dated, "
    "sourced evidence line drawn from the brief — never invent a citation."
)


class LLMAssessor:
    """The production judgment — two Pydantic-AI agents stratified by task difficulty.

    Cost follows difficulty: the cheap **draft** model (``deepseek-v4-flash`` by default) digests
    the bulky raw materials into a compact per-dimension brief; the strong **judge** model
    (``deepseek-v4-pro``) turns that brief into the structured ``ThemeAssessment``. Each tier is a
    :class:`ModelSpec` swapped on its own — a provider-prefixed id, or a ``base_url`` + api-key env
    for any OpenAI-compatible endpoint (Qwen / GLM / Kimi). No fallback chain, no per-model code.
    ``pydantic_ai`` is imported inside the call (the ``discovery`` extra), so the test mode never
    loads it.
    """

    def __init__(self, models: Mapping[str, ModelSpec]) -> None:
        self._models = models

    def _agent(  # pragma: no cover - production engine, host-only deps
        self, role: str, *, output_type: object, instructions: str
    ) -> object:
        import os

        from pydantic_ai import Agent

        spec = self._models[role]
        if spec.base_url is not None:
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            api_key = os.environ[spec.api_key_env] if spec.api_key_env else None
            model: object = OpenAIChatModel(
                spec.model, provider=OpenAIProvider(base_url=spec.base_url, api_key=api_key)
            )
        else:
            model = spec.model
        return Agent(model, output_type=output_type, instructions=instructions)

    def assess(  # pragma: no cover - production engine, host-only deps
        self, topic: TopicTrajectory, evidence_docs: list[StreamDoc]
    ) -> ThemeAssessment:
        docs = [d for d in evidence_docs if d.doc_id in set(topic.doc_ids)] or evidence_docs
        materials = "\n".join(f"[{d.source} {d.as_of}] {d.text}" for d in docs)
        seed = f"Theme: {topic.label}\nConstituents: {', '.join(topic.constituents)}"

        draft = self._agent(DISCOVERY_DRAFT, output_type=str, instructions=_DRAFT_INSTRUCTIONS)
        brief = draft.run_sync(f"{seed}\n\n{materials}").output  # type: ignore[attr-defined]

        judge = self._agent(
            DISCOVERY_JUDGE, output_type=ThemeAssessment, instructions=_JUDGE_INSTRUCTIONS
        )
        result = judge.run_sync(f"{seed}\n\nEvidence brief:\n{brief}")  # type: ignore[attr-defined]
        return result.output  # type: ignore[no-any-return]


def get_assessor(config: Config) -> ThemeAssessor:
    """The production two-tier LLM by default; the deterministic fake in the offline test mode."""

    if config.source == "fixtures":
        return FakeAssessor()
    return LLMAssessor(config.discovery_models)  # pragma: no cover - production engine, host-only
