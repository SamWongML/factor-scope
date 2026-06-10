"""Topic modeling over the text stream — the quantitative half of discovery.

A :class:`TopicModel` groups a rolling corpus into topics and reads each one's *descriptive*
trajectory: the ``acceleration`` (how fast attention is rising), ``base_level`` (its absolute
attention now — a low base leaves room to run), ``breadth`` (distinct corroborating sources), and a
``crowding`` proxy, plus a **noise / weak / strong** label from the topic's popularity trajectory.
This mirrors the BERTrend pattern (process the stream in time slices, classify each topic by a
popularity metric over time) with a handful of economic-meaning constants — never tuned to P&L.

The production engine is :class:`BERTopicModel` — online BERTopic over the live A-share text stream
(the pinned ``discovery`` extra, host-only). :class:`FakeTopicModel` is the deterministic stand-in
the offline test mode swaps in for speed: a no-model frequency grouping that runs the *same*
trajectory reader, so the quantitative contract is identical on either side of one ``Protocol``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from factor_scope.config import Config

__all__ = [
    "BASE_SATURATION",
    "NOISE_MAX_TOTAL",
    "PCA_COMPONENTS",
    "STRONG_MIN_TOTAL",
    "TOPIC_MIN_DOCS",
    "BERTopicModel",
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

# Online-BERTopic plumbing constants (the host engine only; the fake ignores them):
ONLINE_RANDOM_STATE = 42  # pins MiniBatchKMeans so a real run reproduces across invocations
PCA_COMPONENTS = 5  # IncrementalPCA target dimensionality ahead of the online clustering
VECTORIZER_DECAY = 0.01  # OnlineCountVectorizer decay — down-weight stale slices, stay current
MIN_BATCH = 16  # floor on a partial_fit mini-batch so the online sub-models stay numerically sane

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


def _trajectory(
    label: str, docs: list[StreamDoc], *, tokenize: Callable[[str], list[str]] | None = None
) -> TopicTrajectory:
    """Read one topic's descriptive trajectory off its documents (shared, deterministic logic).

    ``tokenize`` segments a document into constituent terms; the offline corpus is pre-segmented so
    it defaults to whitespace, while the host engine passes a Chinese segmenter (jieba).
    """

    tokens_of = (lambda d: tokenize(d.text)) if tokenize is not None else (lambda d: d.tokens())
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
        {tok for d in docs for tok in tokens_of(d) if tok != label and tok not in _STOPWORDS}
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
    """The offline test stand-in: a no-model frequency grouping the suite swaps in for speed.

    Stands in for embedding-based clustering with no model and no randomness: a term that recurs in
    at least :data:`TOPIC_MIN_DOCS` documents is a candidate label; labels are taken most-frequent
    first and each claims the still-unclaimed documents containing it, so the corpus partitions
    deterministically and a document's dominant recurring term names its topic. It reuses the same
    :func:`_trajectory` reader as the production engine, so both sides of the seam agree.
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
    """The production topic engine — online BERTopic over the live A-share text stream.

    Mirrors the BERTrend pattern: local, free multilingual sentence-embeddings feed an online
    BERTopic — ``IncrementalPCA`` → ``MiniBatchKMeans`` (``random_state`` pinned) → an
    ``OnlineCountVectorizer`` with decay, jieba-segmented for Chinese — fitted mini-batch by
    mini-batch in publish-date order via ``partial_fit`` so recent attention is weighted up. Each
    resulting cluster is read by the same :func:`_trajectory` / :func:`classify_trajectory` the
    offline stand-in uses, so the quantitative contract is identical. The heavy ``discovery`` extra
    (bertopic / sentence-transformers / scikit-learn / jieba) is imported inside :meth:`discover`;
    it runs on the host — a Mac mini's MPS or CPU, no paid API — never in the test suite.
    """

    def __init__(self, embedding_model: str, *, n_topics: int) -> None:
        self._embedding_model = embedding_model
        self._n_topics = n_topics

    def discover(  # pragma: no cover - production engine, host-only deps
        self, docs: list[StreamDoc], *, as_of: str
    ) -> list[TopicTrajectory]:
        if not docs:
            return []

        import jieba
        from bertopic import BERTopic
        from bertopic.vectorizers import OnlineCountVectorizer
        from sentence_transformers import SentenceTransformer
        from sklearn.cluster import MiniBatchKMeans
        from sklearn.decomposition import IncrementalPCA

        # Publish-date order: the online stack is input-order-sensitive, so a fixed order (with the
        # pinned random_state) keeps a re-run over the same corpus reproducible.
        ordered = sorted(docs, key=lambda d: (d.as_of, d.doc_id))
        embedder = SentenceTransformer(self._embedding_model)  # auto-selects MPS/CPU, fully local
        embeddings = embedder.encode(
            [d.text for d in ordered], show_progress_bar=False, normalize_embeddings=True
        )

        batches, n_clusters = self._minibatches(ordered)
        n_components = max(1, min(PCA_COMPONENTS, min(len(b) for b in batches)))
        topic_model = BERTopic(
            umap_model=IncrementalPCA(n_components=n_components),
            hdbscan_model=MiniBatchKMeans(n_clusters=n_clusters, random_state=ONLINE_RANDOM_STATE),
            vectorizer_model=OnlineCountVectorizer(tokenizer=jieba.lcut, decay=VECTORIZER_DECAY),
            calculate_probabilities=False,
            verbose=False,
        )

        # Online fit: ``partial_fit`` over the chronological mini-batches; ``topics_`` only reflects
        # the latest batch, so accumulate the per-document assignments as the stream advances.
        assignments: list[int] = []
        cursor = 0
        for batch in batches:
            topic_model.partial_fit(
                [d.text for d in batch], embeddings=embeddings[cursor : cursor + len(batch)]
            )
            assignments.extend(topic_model.topics_)
            cursor += len(batch)

        groups: dict[int, list[StreamDoc]] = {}
        for doc, topic_id in zip(ordered, assignments, strict=True):
            groups.setdefault(topic_id, []).append(doc)

        return [
            _trajectory(
                self._label(topic_model, topic_id, members), members, tokenize=jieba.lcut
            )
            for topic_id, members in groups.items()
        ]

    def _minibatches(  # pragma: no cover - production engine, host-only deps
        self, ordered: list[StreamDoc]
    ) -> tuple[list[list[StreamDoc]], int]:
        """Chronological fixed-size mini-batches for ``partial_fit`` + the cluster count k.

        BERTopic's online tutorial chunks by size (not by date) so each batch is large enough for
        the sub-models; we keep the chunks in publish-date order so decay still favours recent. k
        never exceeds the corpus, and a short trailing batch is merged back so no batch is smaller
        than k (``MiniBatchKMeans`` needs ``n_samples ≥ n_clusters`` to seed centroids).
        """

        n_clusters = min(self._n_topics, len(ordered))
        size = max(n_clusters, PCA_COMPONENTS, MIN_BATCH)
        batches = [ordered[i : i + size] for i in range(0, len(ordered), size)]
        if len(batches) > 1 and len(batches[-1]) < n_clusters:
            batches[-2].extend(batches.pop())
        return batches, n_clusters

    def _label(  # pragma: no cover - production engine, host-only deps
        self, topic_model: object, topic_id: int, members: list[StreamDoc]
    ) -> str:
        """The topic's name — its top c-TF-IDF entity term (cues stripped), with a freq fallback."""

        import jieba

        words = topic_model.get_topic(topic_id) or []  # type: ignore[attr-defined]
        for word, _score in words:
            if word not in _STOPWORDS:
                return str(word)
        freq = Counter(
            tok for d in members for tok in jieba.lcut(d.text) if tok not in _STOPWORDS
        )
        return freq.most_common(1)[0][0] if freq else f"topic-{topic_id}"


def get_topic_model(config: Config) -> TopicModel:
    """The production BERTopic engine by default; the deterministic stand-in in the test mode."""

    if config.source == "fixtures":
        return FakeTopicModel()
    return BERTopicModel(  # pragma: no cover - production engine, host-only deps
        config.discovery_embedding_model, n_topics=config.discovery_n_topics
    )
