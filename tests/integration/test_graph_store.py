"""Integration tests for the durable on-disk connection graph (LadybugDB backend).

Build → persist → reload → query, plus the point-in-time read and building the graph straight from
the weighted holdings readings already in the point-in-time store (CN ``fund_holdings`` + US N-PORT
``edgar``; 13F share rows are skipped).
"""

import pytest

from factor_scope.graph import Edge, LadybugGraphStore, build_graph_from_store
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.integration

Q1 = "2026-03-31"
Q2 = "2026-06-30"


def test_add_edges_returns_count(tmp_path) -> None:
    with LadybugGraphStore(tmp_path / "graph") as graph:
        n = graph.add_edges(
            [Edge(fund="561010", security="中际旭创", weight=0.094, as_of=Q1, source="fh")]
        )
        assert n == 1
        assert graph.count() == 1


def test_graph_persists_across_connections(tmp_path) -> None:
    path = tmp_path / "graph"
    with LadybugGraphStore(path) as graph:
        graph.add_edges(
            [
                Edge(fund="561010", security="中际旭创", weight=0.094, as_of=Q1, source="fh"),
                Edge(fund="515880", security="中际旭创", weight=0.052, as_of=Q1, source="fh"),
            ]
        )
    # Reopen: the durable, append-only graph survives the connection.
    with LadybugGraphStore(path) as graph:
        assert graph.count() == 2
        holders = graph.funds_holding("中际旭创", "2026-12-31")
        assert sorted(e.fund for e in holders) == ["515880", "561010"]
        held = graph.securities_of("561010", "2026-12-31")
        assert [e.security for e in held] == ["中际旭创"]
        assert held[0].weight == pytest.approx(0.094)


def test_graph_query_is_point_in_time(tmp_path) -> None:
    with LadybugGraphStore(tmp_path / "graph") as graph:
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


def test_reingesting_identical_disclosures_is_idempotent(tmp_path) -> None:
    # The graph keys idempotency on the disclosure identity — (endpoints, as_of, source,
    # valid_from). The readings store stays append-only, but at the edge level re-running ingest on
    # the same night's disclosure adds nothing (so look-through weight is never double-counted); a
    # genuinely new disclosure — a new as_of — is still a new row. ``add_edges`` returns the number
    # of edges actually written.
    with LadybugGraphStore(tmp_path / "graph") as graph:
        edge = Edge(fund="F", security="S", weight=0.12, as_of=Q1, source="fund_holdings")
        assert graph.add_edges([edge, edge]) == 1  # same disclosure twice in one batch → one edge
        assert graph.add_edges([edge]) == 0  # re-ingested on a later night → nothing new
        assert graph.count() == 1  # edge count unchanged
        [holder] = graph.funds_holding("S", "2026-12-31")
        assert holder.weight == pytest.approx(0.12)
        # A genuinely new disclosure (a new as_of) is a distinct row, not a dedup.
        later = Edge(fund="F", security="S", weight=0.2, as_of=Q2, source="fund_holdings")
        assert graph.add_edges([later]) == 1
        assert graph.count() == 2


def test_edge_is_live_only_within_its_validity_window(tmp_path) -> None:
    # A holding carries an explicit half-open ``[valid_from, valid_to)`` window. The point-in-time
    # read returns it only inside that window; once valid_to passes the holder drops out entirely
    # (survivorship-aware — not carried forward as if still held), and valid_to is exclusive.
    with LadybugGraphStore(tmp_path / "graph") as graph:
        graph.add_edges(
            [Edge(fund="F", security="S", weight=0.2, as_of=Q1, source="fh",
                  valid_from=Q1, valid_to=Q2)]
        )
        assert graph.funds_holding("S", "2026-01-01") == []  # before valid_from
        assert [e.fund for e in graph.funds_holding("S", "2026-05-01")] == ["F"]  # inside window
        assert graph.funds_holding("S", Q2) == []  # valid_to is exclusive
        assert graph.funds_holding("S", "2026-09-01") == []  # after the window


def test_same_as_of_restatement_returns_a_real_weight_source_pair(tmp_path) -> None:
    # Two disclosures dated the same day (a same-quarter restatement) carry different
    # (weight, source). The point-in-time read must return one *real* edge per holder — weight and
    # source from the SAME disclosure — never a per-column mix (e.g. the larger weight paired with a
    # source that belonged to the other edge), which would inject a value that was never disclosed.
    real_pairs = {(0.10, "zzz"), (0.90, "aaa")}
    with LadybugGraphStore(tmp_path / "graph") as graph:
        graph.add_edges(
            [
                Edge(fund="F", security="S", weight=0.10, as_of=Q1, source="zzz"),
                Edge(fund="F", security="S", weight=0.90, as_of=Q1, source="aaa"),
            ]
        )
        [holder] = graph.funds_holding("S", "2026-12-31")
        assert (holder.weight, holder.source) in real_pairs
        [held] = graph.securities_of("F", "2026-12-31")
        assert (held.weight, held.source) in real_pairs


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
    with LadybugGraphStore(tmp_path / "graph") as graph:
        n = build_graph_from_store(graph, store)
        assert n == 2
        holders = graph.funds_holding("中际旭创", "2026-12-31")
        assert sorted(e.fund for e in holders) == ["515880", "561010"]
    store.close()


def test_build_graph_closes_a_window_when_a_holding_drops_out(tmp_path) -> None:
    # Survivorship through the real ingest path: F discloses S and T at Q1, then a Q2 snapshot that
    # keeps T (reweighted) but drops S. The graph must close each Q1 edge at F's next snapshot (Q2):
    # inside [Q1, Q2) F still holds S; from Q2 on S is gone — not carried forward as if still held —
    # while T reopens with its Q2 weight.
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(series="fund_holdings", key="F/S", as_of=Q1, fetched_at="t",
                    payload={"fund": "F", "holding": "S", "weight": 0.10}),
            Reading(series="fund_holdings", key="F/T", as_of=Q1, fetched_at="t",
                    payload={"fund": "F", "holding": "T", "weight": 0.20}),
            Reading(series="fund_holdings", key="F/T", as_of=Q2, fetched_at="t",
                    payload={"fund": "F", "holding": "T", "weight": 0.25}),
        ]
    )
    with LadybugGraphStore(tmp_path / "graph") as graph:
        build_graph_from_store(graph, store)
        assert [e.fund for e in graph.funds_holding("S", "2026-05-01")] == ["F"]  # inside [Q1, Q2)
        assert graph.funds_holding("S", "2026-07-01") == []  # dropped at Q2 → not carried forward
        held = graph.securities_of("F", "2026-07-01")
        assert [(e.security, e.weight) for e in held] == [("T", pytest.approx(0.25))]
    store.close()


def test_build_graph_includes_nport_edgar_but_not_13f(tmp_path) -> None:
    # US N-PORT fund/ETF holdings carry a weight → graph edges; 13F manager positions carry
    # only shares → not look-through edges (a 13F position has no weight to attribute).
    store = DuckDBStore(":memory:")
    store.append(
        [
            Reading(
                series="edgar",
                key="0000036405/APPLE INC",
                as_of=Q1,
                fetched_at="t",
                payload={"filer": "0000036405", "holding": "APPLE INC", "weight": 0.071},
            ),
            Reading(
                series="edgar",
                key="0001067983/COHR",
                as_of=Q1,
                fetched_at="t",
                payload={"filer": "0001067983", "holding": "COHR", "shares": 1_250_000.0},
            ),
        ]
    )
    with LadybugGraphStore(tmp_path / "graph") as graph:
        n = build_graph_from_store(graph, store)
        assert n == 1  # only the weighted N-PORT row became an edge
        holders = graph.funds_holding("APPLE INC", "2026-12-31")
        assert [e.fund for e in holders] == ["0000036405"]
        assert holders[0].weight == pytest.approx(0.071)
        assert holders[0].source == "edgar"
        assert graph.funds_holding("COHR", "2026-12-31") == []  # 13F shares row is not an edge
    store.close()
