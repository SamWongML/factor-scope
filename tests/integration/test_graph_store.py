"""Integration tests for the durable on-disk connection graph.

Build → persist → reload → query, plus the point-in-time read and building the graph straight from
the ``fund_holdings`` readings already in the point-in-time store.
"""

import pytest

from factor_scope.graph import DuckDBGraphStore, Edge, build_graph_from_store
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.integration

Q1 = "2026-03-31"
Q2 = "2026-06-30"


def test_graph_persists_across_connections(tmp_path) -> None:
    path = tmp_path / "graph.duckdb"
    with DuckDBGraphStore(path) as graph:
        graph.add_edges(
            [
                Edge(fund="561010", security="中际旭创", weight=0.094, as_of=Q1, source="fh"),
                Edge(fund="515880", security="中际旭创", weight=0.052, as_of=Q1, source="fh"),
            ]
        )
    # Reopen: the durable, append-only graph survives the connection.
    with DuckDBGraphStore(path) as graph:
        assert graph.count() == 2
        holders = graph.funds_holding("中际旭创", "2026-12-31")
        assert sorted(e.fund for e in holders) == ["515880", "561010"]
        held = graph.securities_of("561010", "2026-12-31")
        assert [e.security for e in held] == ["中际旭创"]
        assert held[0].weight == pytest.approx(0.094)


def test_graph_query_is_point_in_time(tmp_path) -> None:
    path = tmp_path / "graph.duckdb"
    with DuckDBGraphStore(path) as graph:
        graph.add_edges(
            [
                Edge(fund="F", security="S", weight=0.10, as_of=Q1, source="fund_holdings"),
                Edge(fund="F", security="S", weight=0.18, as_of=Q2, source="fund_holdings"),
            ]
        )
        # As of between the two disclosures, the earlier weight is in force.
        mid = graph.funds_holding("S", "2026-05-01")
        assert len(mid) == 1 and mid[0].weight == pytest.approx(0.10)
        # As of after the later one, the newer weight wins (one row per fund).
        late = graph.funds_holding("S", "2026-07-01")
        assert len(late) == 1 and late[0].weight == pytest.approx(0.18)


def test_build_graph_from_store_reads_fund_holdings(tmp_path) -> None:
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="fund_holdings",
                key="561010/中际旭创",
                as_of=Q1,
                fetched_at="t",
                payload={"fund": "561010", "holding": "中际旭创", "weight": 0.094},
            ),
            Reading(
                series="fund_holdings",
                key="515880/中际旭创",
                as_of=Q1,
                fetched_at="t",
                payload={"fund": "515880", "holding": "中际旭创", "weight": 0.052},
            ),
        ]
    )
    graph = DuckDBGraphStore(tmp_path / "graph.duckdb")
    n = build_graph_from_store(graph, store)
    assert n == 2
    holders = graph.funds_holding("中际旭创", "2026-12-31")
    assert sorted(e.fund for e in holders) == ["515880", "561010"]
    graph.close()
    store.close()
