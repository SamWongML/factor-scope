"""The point-in-time store — append-only, DuckDB-backed.

Every fact the engine ingests becomes a :class:`Reading`: a ``(series, key)`` stamped with both
``as_of`` (when the fact was true / its disclosure date) and ``fetched_at`` (when we pulled it),
plus a JSON ``payload``. Rows are **append-only** — there is no update or delete — so a later
disclosure never rewrites an earlier as-of read.

Reads are point-in-time: :meth:`PointInTimeStore.read_as_of` returns, per key, the latest row with
``as_of <= D``. Reasoning tonight sees only what was knowable tonight. The interface is small and a
:class:`Protocol` so backends stay swappable; the default is :class:`DuckDBStore` (file or
in-memory). DuckDB is the engine.

The silver log is tiered: a recent *hot window* stays in the DuckDB file while older readings are
exported to **Hive-partitioned Parquet** (``series=…/year=…/``) via :meth:`DuckDBStore.tier_cold`,
queried in place. Every read unions the hot table with the cold partitions, so tiering keeps the hot
file bounded as history accrues without moving a single point-in-time result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = ["DuckDBStore", "PointInTimeStore", "Reading"]


class Reading(BaseModel):
    """One stamped, append-only fact in the store."""

    model_config = ConfigDict(frozen=True)

    series: str  # the source/table, e.g. "positions", "prices", "fred"
    key: str  # the entity key within the series, e.g. a fund code or "fund/holding"
    as_of: str  # ISO date the fact was true / its disclosure date (point-in-time)
    fetched_at: str  # ISO timestamp the row was pulled (never the artifact's clock)
    payload: dict[str, Any]  # the row's values


@runtime_checkable
class PointInTimeStore(Protocol):
    """The swappable storage contract. Append-only; reads are point-in-time."""

    def append(self, readings: Iterable[Reading]) -> int:
        """Append rows; return how many were written. Never updates or deletes."""

    def read_as_of(self, series: str, as_of: str) -> list[Reading]:
        """Per key in ``series``, the latest row with ``as_of <= as_of`` (ordered by key)."""

    def history(self, series: str, key: str | None = None) -> list[Reading]:
        """Every row for ``series`` (optionally one ``key``), oldest first — the audit trail."""

    def count(self, series: str | None = None) -> int:
        """Number of rows (in ``series`` if given)."""

    def snapshot_id(self, as_of: str, *, exclude: Iterable[str] = ()) -> str:
        """A deterministic content fingerprint of the store state knowable as of ``as_of``.

        Hashes every reading with ``as_of <= D`` (optionally skipping ``exclude``-ed series) in a
        canonical order, so two stores with identical knowable-by-D facts — excluded series aside —
        share an id and a later disclosure (``as_of > D``) never moves it. Lets a deterministic run
        record *which* frozen snapshot it reasoned over — the seam that reconciles online-default
        with reproducibility.
        """


_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    series VARCHAR NOT NULL,
    key VARCHAR NOT NULL,
    as_of VARCHAR NOT NULL,
    fetched_at VARCHAR NOT NULL,
    payload VARCHAR NOT NULL,
    PRIMARY KEY (series, key, as_of, fetched_at)
);
"""

_COLS = "series, key, as_of, fetched_at, payload"


class DuckDBStore:
    """A DuckDB-backed :class:`PointInTimeStore`. ``path=":memory:"`` for an ephemeral store.

    With ``cold_dir`` set, readings tiered out of the hot file by :meth:`tier_cold` land as
    Hive-partitioned Parquet there, and every read unions the hot table with those partitions.

    ``read_only`` opens the file for reads alone — the seam an isolated query path uses over a
    published replica so it never collides with the nightly writer (see
    :mod:`factor_scope.store.replica`). A read-only handle has no table to create, and refuses
    writes.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        cold_dir: str | Path | None = None,
        read_only: bool = False,
    ) -> None:
        import duckdb  # lazy: the `store` extra is only needed when a store is opened

        self._path = str(path)
        self._cold_dir = Path(cold_dir) if cold_dir is not None else None
        if self._path != ":memory:" and not read_only:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(self._path, read_only=read_only)
        if not read_only:
            self._con.execute(_SCHEMA)

    def _readings(self) -> str:
        """The SQL relation reads run over — the hot table, unioned with cold Parquet when present.

        Tiering is invisible to a point-in-time read: a row reads the same whether it lives in the
        hot DuckDB table or a cold ``series=…/year=…/`` partition.
        """

        if self._cold_dir is not None and any(self._cold_dir.glob("**/*.parquet")):
            glob = str(self._cold_dir / "**" / "*.parquet").replace("'", "''")
            return (
                f"(SELECT {_COLS} FROM readings "
                f"UNION ALL "
                f"SELECT {_COLS} FROM read_parquet('{glob}', hive_partitioning=true))"
            )
        return "readings"

    def append(self, readings: Iterable[Reading]) -> int:
        rows = [
            (
                r.series,
                r.key,
                r.as_of,
                r.fetched_at,
                json.dumps(r.payload, ensure_ascii=False, sort_keys=True),
            )
            for r in readings
        ]
        if not rows:
            return 0
        # Content-addressed revision: a reading is stored only when its payload differs from the
        # latest revision already held for that (series, key, as_of). The nightly ingest re-pulls
        # full histories, so an unchanged bar arrives again every night under a fresh fetched_at —
        # keying the write on content, not the fetch stamp, makes that re-fetch a no-op (cross-
        # night, not just within one run's retries). A genuine restatement (a changed payload) is
        # recorded as a new revision, read_as_of returns the latest. Return how many were written.
        before = self.count()
        self._con.execute(
            "CREATE OR REPLACE TEMP TABLE _incoming "
            "(series VARCHAR, key VARCHAR, as_of VARCHAR, fetched_at VARCHAR, payload VARCHAR)"
        )
        try:
            self._con.executemany("INSERT INTO _incoming VALUES (?, ?, ?, ?, ?)", rows)
            self._con.execute(
                f"""
                INSERT INTO readings ({_COLS})
                SELECT i.series, i.key, i.as_of, i.fetched_at, i.payload
                FROM _incoming i
                LEFT JOIN (
                    SELECT series, key, as_of, payload FROM readings
                    QUALIFY row_number() OVER (
                        PARTITION BY series, key, as_of ORDER BY fetched_at DESC
                    ) = 1
                ) latest
                  ON latest.series = i.series AND latest.key = i.key AND latest.as_of = i.as_of
                WHERE latest.payload IS NULL OR latest.payload <> i.payload
                ON CONFLICT DO NOTHING
                """
            )
        finally:
            self._con.execute("DROP TABLE IF EXISTS _incoming")
        return self.count() - before

    def read_as_of(self, series: str, as_of: str) -> list[Reading]:
        query = f"""
        SELECT {_COLS} FROM {self._readings()}
        WHERE series = ? AND as_of <= ?
        QUALIFY row_number() OVER (PARTITION BY key ORDER BY as_of DESC, fetched_at DESC) = 1
        ORDER BY key
        """
        rows = self._con.execute(query, [series, as_of]).fetchall()
        return [self._to_reading(row) for row in rows]

    def history(self, series: str, key: str | None = None) -> list[Reading]:
        source = self._readings()
        if key is None:
            query = f"SELECT {_COLS} FROM {source} WHERE series = ? ORDER BY key, as_of, fetched_at"
            params: list[str] = [series]
        else:
            query = (
                f"SELECT {_COLS} FROM {source} WHERE series = ? AND key = ? "
                "ORDER BY as_of, fetched_at"
            )
            params = [series, key]
        rows = self._con.execute(query, params).fetchall()
        return [self._to_reading(row) for row in rows]

    def count(self, series: str | None = None) -> int:
        source = self._readings()
        if series is None:
            row = self._con.execute(f"SELECT count(*) FROM {source}").fetchone()
        else:
            row = self._con.execute(
                f"SELECT count(*) FROM {source} WHERE series = ?", [series]
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def snapshot_id(self, as_of: str, *, exclude: Iterable[str] = ()) -> str:
        excluded = sorted(set(exclude))
        clause = f"AND series NOT IN ({', '.join(['?'] * len(excluded))})" if excluded else ""
        rows = self._con.execute(
            f"SELECT {_COLS} FROM {self._readings()} WHERE as_of <= ? {clause} "
            "ORDER BY series, key, as_of, fetched_at, payload",
            [as_of, *excluded],
        ).fetchall()
        # `payload` is already canonical (sorted-key JSON) and the ORDER BY pins row order, so the
        # serialization — hence the digest — is stable across stores and insertion order.
        blob = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def tier_cold(self, cutoff: str) -> int:
        """Move readings with ``as_of < cutoff`` out of the hot file to cold Parquet; return count.

        Old rows are exported to ``cold_dir/series=…/year=…/`` Hive partitions (appended to any that
        already exist) and then pruned from the hot DuckDB table — a physical relocation, not a
        logical edit, so the unioned read is byte-for-byte unchanged. Bounds the hot file as history
        accrues; the recent hot window keyed off the run date stays resident.
        """

        if self._cold_dir is None:
            raise ValueError("tier_cold needs a cold_dir; none was configured")
        row = self._con.execute(
            "SELECT count(*) FROM readings WHERE as_of < ?", [cutoff]
        ).fetchone()
        moved = int(row[0]) if row is not None else 0
        if moved == 0:
            return 0
        self._cold_dir.mkdir(parents=True, exist_ok=True)
        # The COPY target path is a literal (DuckDB ignores a bound parameter there); the cutoff
        # stays bound. ``year`` is derived for the partition layout, then recovered from the path on
        # read, so the cold files carry only the reading columns.
        target = str(self._cold_dir).replace("'", "''")
        self._con.execute(
            f"COPY (SELECT {_COLS}, substr(as_of, 1, 4) AS year FROM readings WHERE as_of < ?) "
            f"TO '{target}' (FORMAT PARQUET, PARTITION_BY (series, year), APPEND)",
            [cutoff],
        )
        self._con.execute("DELETE FROM readings WHERE as_of < ?", [cutoff])
        return moved

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> DuckDBStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _to_reading(row: tuple[Any, ...]) -> Reading:
        series, key, as_of, fetched_at, payload = row
        return Reading(
            series=series,
            key=key,
            as_of=as_of,
            fetched_at=fetched_at,
            payload=json.loads(payload),
        )
