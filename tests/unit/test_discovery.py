"""Theme discovery — the BERTopic-online + LLM machine, exercised on its deterministic fakes.

Discovery turns a rolling text stream into candidate themes for the emerging funnel. Two narrow
seams, each a ``Protocol`` over a production engine, with a deterministic fake the offline suite
swaps in (exercised here):

* a :class:`TopicModel` groups the stream into topics and reads each one's *quantitative*
  trajectory — ``acceleration`` / ``base_level`` / ``breadth`` / ``crowding`` — plus a
  noise/weak/strong popularity label;
* a :class:`ThemeAssessor` populates the four durability/corroboration booleans, **each carrying a
  cited :class:`Evidence`** (the materials the user reads).

``discover_themes`` nets them into ``themes`` Readings the nightly already consumes — dropping
noise, keeping weak/strong, and shaping a payload that ``_theme_from_reading`` turns straight into a
Stage-A ``Theme``. Pure and deterministic given a fixed corpus.
"""

from __future__ import annotations

import pytest

from factor_scope.discovery.assess import FakeAssessor, ThemeAssessment
from factor_scope.discovery.service import discover_themes
from factor_scope.discovery.topics import (
    NOISE_MAX_TOTAL,
    STRONG_MIN_TOTAL,
    FakeTopicModel,
    StreamDoc,
    classify_trajectory,
)

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"
FETCHED_AT = "2026-06-05T22:00:00Z"


def _doc(doc_id: str, as_of: str, source: str, text: str) -> StreamDoc:
    return StreamDoc(doc_id=doc_id, as_of=as_of, source=source, text=text)


# A 储能 cluster: five distinct sources, attention rising toward June, every durability/lead-chain
# cue present and no hype marker. The constituents are the company names mentioned across the docs.
STORAGE_CORPUS = [
    _doc("s1", "2026-05-20", "财新", "储能 装机 宁德时代 阳光电源"),
    _doc("s2", "2026-05-22", "券商研报", "储能 出货 宁德时代 订单 毛利"),
    _doc("s3", "2026-05-28", "上证报", "储能 渗透率 比亚迪 特斯拉"),
    _doc("s4", "2026-06-01", "科创板日报", "储能 放量 亿纬锂能 降本"),
    _doc("s5", "2026-06-03", "财联社", "储能 国轩高科 海外 订单"),
]

# A faint two-mention topic — too little attention to act on — must read as noise and be dropped.
NOISE_CORPUS = [
    _doc("n1", "2026-05-21", "博客", "光伏 某概念"),
    _doc("n2", "2026-05-29", "论坛", "光伏 某说法"),
]


def test_classify_trajectory_reads_noise_weak_strong_from_popularity() -> None:
    assert classify_trajectory([0, 1, 1]) == "noise"  # total at the noise ceiling
    assert classify_trajectory([1, 1, 1]) == "weak"  # rising but still emerging
    assert classify_trajectory([2, 2, 2]) == "strong"  # established attention
    # The thresholds are the economic constants, not magic in the assertions.
    assert sum([0, 1, 1]) <= NOISE_MAX_TOTAL
    assert sum([2, 2, 2]) >= STRONG_MIN_TOTAL


def test_fake_topic_model_finds_the_theme_with_its_quantitative_trajectory() -> None:
    topics = FakeTopicModel().discover(STORAGE_CORPUS, as_of=AS_OF)
    storage = next(t for t in topics if t.label == "储能")

    assert storage.signal in {"weak", "strong"}  # not noise — it advances
    assert set(storage.constituents) >= {"宁德时代", "阳光电源", "比亚迪"}
    assert isinstance(storage.breadth, int) and storage.breadth >= 1
    for value in (storage.acceleration, storage.base_level, storage.crowding):
        assert 0.0 <= value <= 1.0
    assert storage.base_level <= 0.7  # leaves room to run — clears the Stage-A ceiling


def test_fake_topic_model_labels_a_faint_topic_as_noise() -> None:
    topics = FakeTopicModel().discover(NOISE_CORPUS, as_of=AS_OF)
    faint = next(t for t in topics if t.label == "光伏")
    assert faint.signal == "noise"


def test_fake_topic_model_is_deterministic() -> None:
    first = FakeTopicModel().discover(STORAGE_CORPUS, as_of=AS_OF)
    second = FakeTopicModel().discover(STORAGE_CORPUS, as_of=AS_OF)
    assert first == second


def test_fake_assessor_populates_the_four_booleans_each_with_cited_evidence() -> None:
    topics = FakeTopicModel().discover(STORAGE_CORPUS, as_of=AS_OF)
    topic = next(t for t in topics if t.label == "储能")
    assessment = FakeAssessor().assess(topic, STORAGE_CORPUS)

    assert isinstance(assessment, ThemeAssessment)
    for verdict in (
        assessment.broad_adoption,
        assessment.path_to_profit,
        assessment.fad_resistant,
        assessment.lead_chain,
    ):
        assert verdict.holds is True
        assert verdict.evidence.one_line  # every populated field carries a cited line
        assert verdict.evidence.as_of  # dated, sourced — fetch, don't recall


def test_fake_assessor_flags_a_hype_theme_as_not_fad_resistant() -> None:
    fad_corpus = [
        _doc("f1", "2026-05-20", "雪球", "元宇宙 概念 炒作 数字王国"),
        _doc("f2", "2026-05-27", "论坛", "元宇宙 题材 创维数字"),
        _doc("f3", "2026-06-02", "贴吧", "元宇宙 蹭热点 佳创视讯"),
    ]
    topics = FakeTopicModel().discover(fad_corpus, as_of=AS_OF)
    topic = next(t for t in topics if t.label == "元宇宙")
    assessment = FakeAssessor().assess(topic, fad_corpus)
    assert assessment.fad_resistant.holds is False


def test_discover_themes_emits_schema_shaped_readings_dropping_noise() -> None:
    docs = STORAGE_CORPUS + NOISE_CORPUS
    readings = discover_themes(
        docs, FakeTopicModel(), FakeAssessor(), as_of=AS_OF, fetched_at=FETCHED_AT
    )

    keys = {r.key for r in readings}
    assert "储能" in keys  # the strong theme is written
    assert "光伏" not in keys  # noise is dropped, never written

    storage = next(r for r in readings if r.key == "储能")
    assert storage.series == "themes"
    assert storage.as_of == AS_OF
    assert storage.fetched_at == FETCHED_AT
    payload = storage.payload
    # Schema-shaped: the quantitative fields, the constituents seed, the four booleans, + evidence.
    for field in (
        "acceleration",
        "base_level",
        "breadth",
        "crowding",
        "constituents",
        "broad_adoption",
        "path_to_profit",
        "fad_resistant",
        "lead_chain",
        "evidence",
    ):
        assert field in payload
    assert payload["evidence"]  # the materials the user reads travel with the theme
    assert all({"src", "as_of", "one_line"} <= set(e) for e in payload["evidence"])


def test_discover_themes_is_deterministic() -> None:
    docs = STORAGE_CORPUS + NOISE_CORPUS
    first = discover_themes(
        docs, FakeTopicModel(), FakeAssessor(), as_of=AS_OF, fetched_at=FETCHED_AT
    )
    second = discover_themes(
        docs, FakeTopicModel(), FakeAssessor(), as_of=AS_OF, fetched_at=FETCHED_AT
    )
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]
