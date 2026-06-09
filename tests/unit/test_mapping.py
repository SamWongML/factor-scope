"""Unit tests for the inferred theme→fund mapping (overlap 重合度 + correlation 涨跌幅相关性).

The mapping derives each theme's candidate funds from data — holdings overlap over the look-through
graph, confirmed by rolling return correlation — replacing any hand-tagged table. Behavior is
exercised through the public :func:`infer_links` / :func:`return_correlation`, never internals.
"""

import pytest

from factor_scope.emerging.mapping import MIN_OVERLAP, infer_links, return_correlation
from factor_scope.graph import Edge, LadybugGraphStore
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.unit

AS_OF = "2026-06-05"

# 储能 reference constituents — battery / energy-storage names.
STORAGE = ["宁德时代", "阳光电源", "比亚迪", "亿纬锂能", "国轩高科"]


def _graph(edges: list[tuple[str, str, float]]) -> LadybugGraphStore:
    graph = LadybugGraphStore(":memory:")
    graph.add_edges(
        [Edge(fund=f, security=s, weight=w, as_of="2026-03-31", source="t") for f, s, w in edges]
    )
    return graph


def _navs(store: DuckDBStore, code: str, navs: list[float]) -> None:
    store.append(
        [
            Reading(
                series="prices",
                key=code,
                as_of=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                fetched_at="2026-06-05T22:00:00Z",
                payload={"nav": nav},
            )
            for i, nav in enumerate(navs)
        ]
    )


def test_overlap_counts_only_constituent_weights() -> None:
    # Fund X holds two 储能 constituents plus 中际旭创 (an optical name, not in the set).
    graph = _graph(
        [("X", "宁德时代", 0.16), ("X", "阳光电源", 0.11), ("X", "中际旭创", 0.05)]
    )
    links = infer_links({"储能": STORAGE}, ["X"], graph, DuckDBStore(":memory:"), AS_OF)
    assert len(links) == 1
    link = links[0]
    assert link.theme == "储能"
    assert link.code == "X"
    assert link.overlap == pytest.approx(0.27)  # 0.16 + 0.11; 中际旭创 excluded
    assert set(link.overlap_names) == {"宁德时代", "阳光电源"}


def test_funds_below_min_overlap_are_not_candidates() -> None:
    # FAINT carries only 0.05 of one constituent — below the 0.10 floor → not a candidate at all.
    graph = _graph([("PURE", "宁德时代", 0.30), ("FAINT", "宁德时代", 0.05)])
    links = infer_links({"储能": STORAGE}, ["PURE", "FAINT"], graph, DuckDBStore(":memory:"), AS_OF)
    assert [link.code for link in links] == ["PURE"]
    assert MIN_OVERLAP == 0.10  # the floor is an economic constant, pinned here


def test_links_rank_by_overlap_then_code() -> None:
    # Three candidates clear the floor; without correlation the score is the overlap, so the ranking
    # is overlap-desc. B and C tie on overlap (0.20) → the code breaks the tie deterministically.
    graph = _graph(
        [
            ("A", "宁德时代", 0.40),
            ("C", "比亚迪", 0.20),
            ("B", "阳光电源", 0.20),
        ]
    )
    links = infer_links({"储能": STORAGE}, ["A", "B", "C"], graph, DuckDBStore(":memory:"), AS_OF)
    assert [link.code for link in links] == ["A", "B", "C"]
    assert all(link.correlation is None for link in links)  # no prices → correlation absent
    assert links[0].score == pytest.approx(0.40)  # score is the overlap when correlation is None


def test_return_correlation_degrades_to_none_without_enough_prices() -> None:
    store = DuckDBStore(":memory:")
    _navs(store, "F", [1.00, 1.01, 1.02])  # only two return points → below CORR_MIN_POINTS
    _navs(store, "宁德时代", [1.00, 1.01, 1.02])
    assert return_correlation(store, "F", AS_OF, ["宁德时代"]) is None
    # Absent constituent prices also degrade to None rather than raising.
    rich = DuckDBStore(":memory:")
    _navs(rich, "F", [1.0 + 0.01 * i for i in range(30)])
    assert return_correlation(rich, "F", AS_OF, ["宁德时代"]) is None


def test_return_correlation_reads_co_movement() -> None:
    store = DuckDBStore(":memory:")
    rising = [1.0 + 0.01 * i for i in range(30)]
    _navs(store, "F", rising)
    _navs(store, "宁德时代", rising)  # fund tracks the reference name day-for-day
    corr = return_correlation(store, "F", AS_OF, ["宁德时代"])
    assert corr is not None
    assert corr == pytest.approx(1.0)


def _oscillating(up_first: bool) -> list[float]:
    nav, out = 1.0, [1.0]
    for i in range(30):
        nav *= 1.02 if (i % 2 == 0) is up_first else 0.98
        out.append(nav)
    return out


def test_correlation_confirms_overlap_in_the_ranking() -> None:
    # Two candidates with identical overlap (0.30). The reference name oscillates; the co-mover
    # tracks it (corr→+1), the contrarian moves opposite (corr→−1). Co-movement confirms the
    # exposure, so the co-mover outranks the contrarian — even though its code sorts later.
    graph = _graph([("ZMOVER", "宁德时代", 0.30), ("ACONTRA", "宁德时代", 0.30)])
    store = DuckDBStore(":memory:")
    _navs(store, "宁德时代", _oscillating(up_first=True))
    _navs(store, "ZMOVER", _oscillating(up_first=True))
    _navs(store, "ACONTRA", _oscillating(up_first=False))

    links = infer_links({"储能": STORAGE}, ["ACONTRA", "ZMOVER"], graph, store, AS_OF)
    assert [link.code for link in links] == ["ZMOVER", "ACONTRA"]
    mover, contra = links
    assert mover.correlation == pytest.approx(1.0)
    assert contra.correlation == pytest.approx(-1.0)
    assert mover.score == pytest.approx(0.30)  # 0.30 * (0.5 + 0.5*1.0)
    assert contra.score == pytest.approx(0.15)  # 0.30 * (0.5 + 0.5*max(0,-1))


def test_infer_links_is_deterministic() -> None:
    graph = _graph(
        [("X", "宁德时代", 0.30), ("X", "阳光电源", 0.10), ("Y", "比亚迪", 0.22)]
    )
    store = DuckDBStore(":memory:")
    _navs(store, "宁德时代", _oscillating(up_first=True))
    _navs(store, "X", _oscillating(up_first=True))
    args = ({"储能": STORAGE, "元宇宙": ["数字王国"]}, ["Y", "X"], graph, store, AS_OF)
    assert infer_links(*args) == infer_links(*args)
