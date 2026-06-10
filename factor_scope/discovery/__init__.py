"""Theme discovery — the user/cron-triggered service that feeds the emerging funnel.

A separate entrypoint from the nightly (it sits on the *research* side of the snapshot boundary):
it turns a rolling text stream into candidate ``themes`` Readings. Two narrow seams, each a
``Protocol`` over a production engine — behind the pinned ``discovery`` extra, host-only — with a
deterministic fake the offline test mode swaps in: a :class:`TopicModel` (online BERTopic, the
quantitative trajectory) and a :class:`ThemeAssessor` (a cost-stratified LLM filling the
durability/corroboration fields with cited evidence).
"""

from __future__ import annotations

from factor_scope.discovery.assess import (
    FakeAssessor,
    FieldVerdict,
    LLMAssessor,
    ThemeAssessment,
    ThemeAssessor,
    get_assessor,
)
from factor_scope.discovery.service import build_stream_docs, discover_themes
from factor_scope.discovery.topics import (
    FakeTopicModel,
    StreamDoc,
    TopicModel,
    TopicSignal,
    TopicTrajectory,
    classify_trajectory,
    get_topic_model,
)

__all__ = [
    "FakeAssessor",
    "FakeTopicModel",
    "FieldVerdict",
    "LLMAssessor",
    "StreamDoc",
    "ThemeAssessment",
    "ThemeAssessor",
    "TopicModel",
    "TopicSignal",
    "TopicTrajectory",
    "build_stream_docs",
    "classify_trajectory",
    "discover_themes",
    "get_assessor",
    "get_topic_model",
]
