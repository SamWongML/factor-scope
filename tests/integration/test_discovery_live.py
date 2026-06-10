"""Host-only integration test for the production discovery engine (online BERTopic + jieba).

Skips unless the ``discovery`` extra is installed — CI installs only ``.[dev,store]`` — so it never
runs in the suite's offline path. On the host it exercises what :class:`FakeTopicModel` stands in
for: *unsegmented* Chinese documents clustered by embeddings, read into the same ``TopicTrajectory``
contract the offline stub produces. Needs network on first run to fetch the embedding model.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_online_bertopic_clusters_unsegmented_chinese_into_trajectories() -> None:
    pytest.importorskip("bertopic")
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("jieba")

    from factor_scope.discovery.topics import BERTopicModel, StreamDoc

    # Raw, unsegmented Chinese (no whitespace) — only jieba can tokenise it for the vectorizer.
    corpus = [
        StreamDoc("s1", "2026-05-20", "财新", "储能装机持续放量宁德时代领跑"),
        StreamDoc("s2", "2026-05-22", "券商研报", "储能出货高增宁德时代订单饱满毛利改善"),
        StreamDoc("s3", "2026-05-28", "上证报", "储能渗透率提升比亚迪特斯拉共振"),
        StreamDoc("s4", "2026-06-01", "科创板日报", "储能放量亿纬锂能降本提速"),
        StreamDoc("s5", "2026-06-03", "财联社", "储能国轩高科海外订单落地"),
        StreamDoc("m1", "2026-05-21", "雪球", "元宇宙概念炒作数字王国热度回升"),
        StreamDoc("m2", "2026-05-27", "贴吧", "元宇宙题材蹭热点创维数字异动"),
    ]
    topics = BERTopicModel("paraphrase-multilingual-MiniLM-L12-v2", n_topics=2).discover(
        corpus, as_of="2026-06-05"
    )

    assert topics
    # Every document lands in exactly one topic — the trajectories partition the corpus.
    assigned = [doc_id for t in topics for doc_id in t.doc_ids]
    assert sorted(assigned) == sorted(d.doc_id for d in corpus)
    for t in topics:
        assert t.label  # named for an entity term, cues stripped
        assert t.signal in {"noise", "weak", "strong"}
        assert t.breadth >= 1
        for value in (t.acceleration, t.base_level, t.crowding):
            assert 0.0 <= value <= 1.0

    # The five 储能 documents should dominate one topic — embedding clustering, not keyword match.
    storage = {"s1", "s2", "s3", "s4", "s5"}
    best = max(topics, key=lambda t: len(storage.intersection(t.doc_ids)))
    assert len(storage.intersection(best.doc_ids)) >= 4
