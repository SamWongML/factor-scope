"""The isolated read-only query seam — a bounded pool over a post-run store replica.

Pre-materialized trails (see :mod:`factor_scope.series`) take the common charting case off the
engine entirely. This is the bounded *escape hatch* for genuine ad-hoc queries: rather than open
the live store the nightly writer holds — DuckDB enforces one read-write **or** many read-only
processes, never both — a run publishes a read-only **replica** of the store, and queries open
*that*. The reader never collides with the writer (different files), and every query runs through a
**bounded connection pool** with a per-query memory cap, row cap, and timeout, so one heavy query
can't starve the box.
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb


class QueryTimeout(RuntimeError):
    """A pooled query ran past its deadline and was interrupted — the per-query time cap."""


def publish_replica(store_path: str | Path, replica_path: str | Path) -> Path:
    """Copy the committed store file to ``replica_path`` atomically; return the replica path.

    Called after the nightly writer has closed (so the file is checkpointed), so the replica is a
    point-in-time copy a read-only query pool can open without ever touching the writer's handle.
    Staged beside the target and renamed, so a concurrent reader never opens a half-written replica.

    This copies the *hot* store file only. With a cold tier configured, the older readings already
    live in their own read-only ``cold_dir`` Parquet and are queried in place — so a reader that
    needs the full history opens the replica as ``DuckDBStore(replica, read_only=True, cold_dir=…)``
    (its reads union hot + cold) rather than the hot ``readings`` table alone.
    """

    replica = Path(replica_path)
    replica.parent.mkdir(parents=True, exist_ok=True)
    staging = replica.with_name(replica.name + ".staging")
    shutil.copyfile(store_path, staging)
    os.replace(staging, replica)
    return replica


class ReadReplica:
    """A bounded, read-only query pool over a store replica — the isolated ad-hoc query path.

    Opens ``pool_size`` read-only connections to the replica up front; :meth:`query` checks one out
    (blocking when all are busy, so concurrency is bounded) and runs the statement with a memory
    cap, a row cap, and a wall-clock timeout. None of this can reach the live store — the replica is
    a separate file — so concurrent reads never block the nightly writer.

    The replica is the *hot* store file; with a cold tier configured the older readings live in
    ``cold_dir`` Parquet, untouched by :func:`publish_replica`. For full point-in-time history over
    the replica, open it as ``DuckDBStore(replica, read_only=True, cold_dir=…)`` — its reads union
    hot + cold — rather than querying the hot ``readings`` table alone through this pool.
    """

    def __init__(
        self,
        replica_path: str | Path,
        *,
        pool_size: int = 4,
        memory_limit: str = "256MB",
        row_limit: int = 10_000,
        timeout_s: float = 5.0,
    ) -> None:
        import duckdb  # lazy: the `store` extra is only needed when a replica is opened

        self._row_limit = row_limit
        self._timeout_s = timeout_s
        self._pool: queue.Queue[duckdb.DuckDBPyConnection] = queue.Queue(maxsize=pool_size)
        for _ in range(pool_size):
            con = duckdb.connect(str(replica_path), read_only=True)
            # Bound the memory one query may claim so a heavy escape-hatch read can't starve the
            # box.
            con.execute(f"SET memory_limit='{memory_limit}'")
            self._pool.put(con)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Run one read-only statement, bounded by the pool's row + time caps; return its rows.

        At most ``row_limit`` rows come back (the result is never unbounded), and a statement that
        runs past ``timeout_s`` is interrupted and raises :class:`QueryTimeout`.
        """

        con = self._pool.get()  # blocks when the pool is exhausted → concurrency is bounded
        try:
            return self._run_bounded(con, sql, params)
        finally:
            self._pool.put(con)

    def _run_bounded(
        self, con: duckdb.DuckDBPyConnection, sql: str, params: tuple[Any, ...]
    ) -> list[tuple[Any, ...]]:
        out: dict[str, Any] = {}

        def work() -> None:
            try:
                out["rows"] = con.execute(sql, list(params)).fetchmany(self._row_limit)
            except BaseException as exc:  # surface to the caller's thread, including an interrupt
                out["err"] = exc

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(self._timeout_s)
        if worker.is_alive():
            con.interrupt()  # cancel the running statement; the worker's execute then raises
            worker.join()
            raise QueryTimeout(f"query exceeded {self._timeout_s}s and was interrupted")
        if "err" in out:
            raise out["err"]
        rows: list[tuple[Any, ...]] = out["rows"]
        return rows

    def close(self) -> None:
        while not self._pool.empty():
            self._pool.get().close()

    def __enter__(self) -> ReadReplica:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
