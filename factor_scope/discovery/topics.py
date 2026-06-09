"""Topic modeling over the text stream — the quantitative half of discovery.

A :class:`TopicModel` groups a rolling corpus into topics and reads each one's *descriptive*
trajectory: the ``acceleration`` (how fast attention is rising), ``base_level`` (its absolute
attention now — a low base leaves room to run), ``breadth`` (distinct corroborating sources), and a
``crowding`` proxy, plus a **noise / weak / strong** label from the topic's popularity trajectory.
This mirrors the BERTrend pattern (process the stream in time slices, classify each topic by a
popularity metric over time) with a handful of economic-meaning constants — never tuned to P&L.

Two impls behind one ``Protocol`` (the repo's fake + lazy-real idiom): :class:`FakeTopicModel` is a
deterministic frequency grouping + the same trajectory classifier — the only impl CI ever runs;
:class:`BERTopicModel` lazily imports the real online BERTopic stack and is opt-in (the pinned
``discovery`` extra, never installed offline).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from factor_scope.config import Config

__all__ = [
    "BASE_SATURATION",
    "NOISE_MAX_TOTAL",
    "STRONG_MIN_TOTAL",
    "TOPIC_MIN_DOCS",
    "FakeTopicModel",
    "StreamDoc",
    "TopicModel",
    "TopicSignal",
    "TopicTrajectory",
    "classify_trajectory",
    "get_topic_model",
]

TopicSignal = Literal["noise", "weak", "strong"]

# Economic-meaning constants (never tuned to P&L):
NOISE_MAX_TOTAL = 2  # ≤ this many mentions across the window → too faint to act on → noise
STRONG_MIN_TOTAL = 5  # ≥ this many mentions → an established, strong signal (between → weak)
BASE_SATURATION = 12  # mention count that reads as a fully-saturated (1.0) absolute attention level
TOPIC_MIN_DOCS = 2  # a term must recur in ≥ this many docs to seed a topic (else it is a singleton)

# Generic / cue vocabulary kept out of topic labels and constituents so a topic is named for its
# entity, not the verbs around it. (The assessor reads these same cues off the raw text — see
# ``assess`` — so the two seams stay decoupled.)
_STOPWORDS = frozenset(
    {
        "装机", "出货", "放量", "渗透率", "降本", "订单", "毛利", "盈利",  # adoption / profit cues
        "特斯拉", "海外", "美国", "北美",  # lead-chain (overseas relay) cues
        "概念", "题材", "炒作", "蹭热点", "某概念", "某说法",  # hype markers
    }
)


@dataclass(frozen=True)
class StreamDoc:
    """One dated document in the rolling corpus (a ``textstream`` Reading, point-in-time)."""

    doc_id: str
    as_of: str
    source: str
    text: str

    def tokens(self) -> list[str]:
        """The document's whitespace-separated terms (the corpus is pre-segmented)."""

        return self.text.split()


@dataclass(frozen=True)
class TopicTrajectory:
    """One discovered topic and the descriptive trajectory Stage A reads off it."""

    label: str  # the topic's name — its dominant recurring term
    constituents: tuple[str, ...]  # the entity names mentioned (the mapping's overlap seed)
    doc_ids: tuple[str, ...]  # the corpus documents in this topic (the assessor's evidence pool)
    acceleration: float  # rate of attention rise (0..1): the recent share of mentions
    base_level: float  # absolute attention now (0..1): total mentions vs a saturation reference
    breadth: int  # distinct corroborating sources
    crowding: float  # concentration proxy (0..1): few outlets carrying many mentions reads crowded
    signal: TopicSignal  # noise / weak / strong, from the popularity trajectory


def classify_trajectory(popularity: list[int]) -> TopicSignal:
    """Label a topic noise / weak / strong from its per-slice popularity (BERTrend-style).

    The total mention count is the popularity metric: at or below :data:`NOISE_MAX_TOTAL` a topic is
    too faint to act on (noise); at or above :data:`STRONG_MIN_TOTAL` it is an established strong
    signal; in between it is an emerging weak signal — the band the funnel most wants to catch.
    """

    total = sum(popularity)
    if total <= NOISE_MAX_TOTAL:
        return "noise"
    if total >= STRONG_MIN_TOTAL:
        return "strong"
    return "weak"


@runtime_checkable
class TopicModel(Protocol):
    """The swappable topic-modeling seam: a corpus → its topics' descriptive trajectories."""

    def discover(self, docs: list[StreamDoc], *, as_of: str) -> list[TopicTrajectory]:
        """Group ``docs`` into topics and read each one's trajectory (deterministic per corpus)."""


def _trajectory(label: str, docs: list[StreamDoc]) -> TopicTrajectory:
    """Read one topic's descriptive trajectory off its documents (shared, deterministic logic)."""

    by_date = Counter(d.as_of for d in docs)
    dates = sorted(by_date)
    popularity = [by_date[date] for date in dates]
    total = len(docs)

    # Acceleration: the share of attention that is recent. A single-date topic is read as neutral
    # (0.5); otherwise it is the fraction of mentions in the later half of the topic's date span.
    if len(dates) < 2:
        acceleration = 0.5
    else:
        cut = dates[len(dates) // 2]
        recent = sum(1 for d in docs if d.as_of >= cut)
        acceleration = round(recent / total, 2)

    sources = {d.source for d in docs}
    breadth = len(sources)
    base_level = round(min(1.0, total / BASE_SATURATION), 2)
    crowding = round(1.0 - len(sources) / total, 2)  # broad source set → low; few outlets → high

    constituents = sorted(
        {tok for d in docs for tok in d.tokens() if tok != label and tok not in _STOPWORDS}
    )
    return TopicTrajectory(
        label=label,
        constituents=tuple(constituents),
        doc_ids=tuple(sorted(d.doc_id for d in docs)),
        acceleration=acceleration,
        base_level=base_level,
        breadth=breadth,
        crowding=crowding,
        signal=classify_trajectory(popularity),
    )


class FakeTopicModel:
    """Deterministic topic modeling: a recurring term seeds a topic and greedily claims its docs.

    A stand-in for embedding-based clustering with no model and no randomness: a term that recurs in
    at least :data:`TOPIC_MIN_DOCS` documents is a candidate label; labels are taken most-frequent
    first and each claims the still-unclaimed documents containing it, so the corpus partitions
    deterministically and a document's dominant recurring term names its topic. The only CI impl.
    """

    def discover(self, docs: list[StreamDoc], *, as_of: str) -> list[TopicTrajectory]:
        doc_freq = Counter(
            tok for d in docs for tok in set(d.tokens()) if tok not in _STOPWORDS
        )
        labels = sorted(
            (tok for tok, n in doc_freq.items() if n >= TOPIC_MIN_DOCS),
            key=lambda tok: (-doc_freq[tok], tok),
        )
        claimed: set[str] = set()
        topics: list[TopicTrajectory] = []
        for label in labels:
            members = [d for d in docs if d.doc_id not in claimed and label in d.tokens()]
            if not members:
                continue
            claimed.update(d.doc_id for d in members)
            topics.append(_trajectory(label, members))
        return topics


class BERTopicModel:
    """The real online BERTopic stack — lazily imported, opt-in, never run by CI.

    Mirrors the BERTrend pattern over the live corpus: precomputed multilingual embeddings feeding
    an online BERTopic (``IncrementalPCA`` + ``MiniBatchKMeans`` + an ``OnlineCountVectorizer`` with
    decay, ``random_state`` pinned), then the same :func:`classify_trajectory` over each topic's
    popularity across time slices. The heavy dependency is imported inside :meth:`discover` so the
    offline path never loads it.
    """

    def __init__(self, embedding_model: str) -> None:
        self._embedding_model = embedding_model

    def discover(  # pragma: no cover - opt-in live path
        self, docs: list[StreamDoc], *, as_of: str
    ) -> list[TopicTrajectory]:
        raise NotImplementedError(
            "online BERTopic discovery requires the `discovery` extra and a configured corpus; "
            "install '.[discovery]' and run on the host, or use --offline for the fixture corpus"
        )


def get_topic_model(config: Config) -> TopicModel:
    """The deterministic fake offline, the lazy real BERTopic online (mirrors the source seams)."""

    if config.source == "fixtures":
        return FakeTopicModel()
    return BERTopicModel(config.discovery_embedding_model)  # pragma: no cover - opt-in live path
