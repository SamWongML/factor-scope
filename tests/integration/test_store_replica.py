"""The isolated read-only query seam — a bounded pool over a post-run store replica.

The contract: an ad-hoc query opens the store **read-only against a replica**, never the writer's
handle; per-query memory / time / row caps are enforced; and concurrent reads never block the
nightly writer (DuckDB's one-RW-or-many-RO rule, satisfied structurally by the separate file).
"""

from __future__ import annotations

import duckdb
import pytest

from factor_scope.store import DuckDBStore, Reading
from factor_scope.store.replica import QueryTimeout, ReadReplica, publish_replica

pytestmark = pytest.mark.integration


def _seed_store(path) -> None:
    store = DuckDBStore(path)
    try:
        store.append(
            [
                Reading(series="prices", key="A", as_of="2026-06-05", fetched_at="t",
                        payload={"nav": 1.0}),
                Reading(series="prices", key="B", as_of="2026-06-05", fetched_at="t",
                        payload={"nav": 2.0}),
            ]
        )
    finally:
        store.close()


def _bytes(memory_limit: str) -> float:
    """Parse a DuckDB ``current_setting('memory_limit')`` value (e.g. ``'256.0 MiB'``) to bytes."""

    value, unit = memory_limit.split()
    scale = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4, "B": 1, "bytes": 1}
    return float(value) * scale[unit]


def test_publish_replica_is_an_independent_readable_copy(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    replica = publish_replica(store_path, tmp_path / "store.replica.duckdb")

    pool = ReadReplica(replica)
    try:
        rows = pool.query("SELECT count(*) FROM readings")
    finally:
        pool.close()
    assert rows == [(2,)]


def test_a_query_reads_the_replica_while_the_writer_holds_the_store(tmp_path) -> None:
    # The whole point of the replica: a read can run while the nightly writer holds the live store
    # read-write — they are different files, so DuckDB's one-RW-or-many-RO rule is never tripped.
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    publish_replica(store_path, tmp_path / "store.replica.duckdb")

    writer = DuckDBStore(store_path)  # holds the live store read-write
    try:
        pool = ReadReplica(tmp_path / "store.replica.duckdb")
        try:
            # Both succeed concurrently — the reader never collides with the writer.
            assert pool.query("SELECT count(*) FROM readings") == [(2,)]
            writer.append([Reading(series="prices", key="C", as_of="2026-06-06",
                                   fetched_at="t", payload={"nav": 3.0})])
        finally:
            pool.close()
    finally:
        writer.close()


def test_a_read_only_store_refuses_to_write(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    ro = DuckDBStore(store_path, read_only=True)
    try:
        assert ro.count("prices") == 2  # reads are fine
        with pytest.raises(duckdb.Error):  # a write through a read-only handle is refused
            ro.append([Reading(series="prices", key="Z", as_of="2026-06-05",
                               fetched_at="t", payload={"nav": 9.0})])
    finally:
        ro.close()


def test_the_row_cap_bounds_the_result(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    publish_replica(store_path, tmp_path / "r.duckdb")

    pool = ReadReplica(tmp_path / "r.duckdb", row_limit=10)
    try:
        rows = pool.query("SELECT * FROM range(1000)")
        assert len(rows) == 10  # never the full thousand — the result is bounded
    finally:
        pool.close()


def test_the_memory_cap_is_applied_per_connection(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    publish_replica(store_path, tmp_path / "r.duckdb")

    pool = ReadReplica(tmp_path / "r.duckdb", memory_limit="256MB")
    try:
        [(setting,)] = pool.query("SELECT current_setting('memory_limit')")
    finally:
        pool.close()
    # The per-query memory ceiling is in force — well under a default that is ~80% of host RAM.
    assert _bytes(setting) <= 256 * 1024**2


def test_a_query_past_its_deadline_times_out(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    publish_replica(store_path, tmp_path / "r.duckdb")

    pool = ReadReplica(tmp_path / "r.duckdb", timeout_s=0.2)
    try:
        with pytest.raises(QueryTimeout):
            # A genuine 10^10-multiply aggregation the optimizer can't shortcut — interrupted well
            # before it finishes, so the time cap holds.
            pool.query("SELECT sum(a.i * b.i) FROM range(100000) a(i), range(100000) b(i)")
    finally:
        pool.close()


def test_the_pool_is_reusable_after_a_timeout(tmp_path) -> None:
    # An interrupted query must return its connection to the pool intact for the next caller.
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    publish_replica(store_path, tmp_path / "r.duckdb")

    pool = ReadReplica(tmp_path / "r.duckdb", pool_size=1, timeout_s=0.2)
    try:
        with pytest.raises(QueryTimeout):
            pool.query("SELECT sum(a.i * b.i) FROM range(100000) a(i), range(100000) b(i)")
        assert pool.query("SELECT count(*) FROM readings") == [(2,)]  # connection recovered
    finally:
        pool.close()


def test_the_replica_path_is_returned_and_atomic(tmp_path) -> None:
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    target = tmp_path / "nested" / "store.replica.duckdb"
    out = publish_replica(store_path, target)
    assert out == target and target.is_file()
    # No staging file is left behind.
    assert not (target.parent / (target.name + ".staging")).exists()


def test_a_published_replica_carries_the_schema_and_rows(tmp_path) -> None:
    # A regression guard that the copy is a real DuckDB store, opened read-only, not a placeholder.
    store_path = tmp_path / "store.duckdb"
    _seed_store(store_path)
    publish_replica(store_path, tmp_path / "r.duckdb")
    ro = DuckDBStore(tmp_path / "r.duckdb", read_only=True)
    try:
        assert ro.count("prices") == 2
    finally:
        ro.close()
