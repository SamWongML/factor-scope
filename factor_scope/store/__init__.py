"""The point-in-time store — append-only, DuckDB-backed.

Every fact the engine ingests becomes a :class:`Reading`: a ``(series, key)`` stamped with both
``as_of`` (when the fact was true / its disclosure date) and ``fetched_at`` (when we pulled it),
plus a JSON ``payload``. Rows are **append-only** — there is no update or delete — so a later
disclosure never rewrites an earlier as-of read.

Reads are point-in-time: :meth:`PointInTimeStore.read_as_of` returns, per key, the latest row with
``as_of <= D``. Reasoning tonight sees only what was knowable tonight. The interface is small and a
:class:`Protocol` so backends stay swappable; the default is :class:`DuckDBStore` (file or
in-memory). DuckDB is the engine and Parquet the cold export format (``export_parquet``).
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
    """A DuckDB-backed :class:`PointInTimeStore`. ``path=":memory:"`` for an ephemeral store."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        import duckdb  # lazy: the `store` extra is only needed when a store is opened

        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(self._path)
        self._con.execute(_SCHEMA)

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
        SELECT {_COLS} FROM readings
        WHERE series = ? AND as_of <= ?
        QUALIFY row_number() OVER (PARTITION BY key ORDER BY as_of DESC, fetched_at DESC) = 1
        ORDER BY key
        """
        rows = self._con.execute(query, [series, as_of]).fetchall()
        return [self._to_reading(row) for row in rows]

    def history(self, series: str, key: str | None = None) -> list[Reading]:
        if key is None:
            query = f"SELECT {_COLS} FROM readings WHERE series = ? ORDER BY key, as_of, fetched_at"
            params: list[str] = [series]
        else:
            query = (
                f"SELECT {_COLS} FROM readings WHERE series = ? AND key = ? "
                "ORDER BY as_of, fetched_at"
            )
            params = [series, key]
        rows = self._con.execute(query, params).fetchall()
        return [self._to_reading(row) for row in rows]

    def count(self, series: str | None = None) -> int:
        if series is None:
            row = self._con.execute("SELECT count(*) FROM readings").fetchone()
        else:
            row = self._con.execute(
                "SELECT count(*) FROM readings WHERE series = ?", [series]
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def snapshot_id(self, as_of: str, *, exclude: Iterable[str] = ()) -> str:
        excluded = sorted(set(exclude))
        clause = f"AND series NOT IN ({', '.join(['?'] * len(excluded))})" if excluded else ""
        rows = self._con.execute(
            f"SELECT {_COLS} FROM readings WHERE as_of <= ? {clause} "
            "ORDER BY series, key, as_of, fetched_at, payload",
            [as_of, *excluded],
        ).fetchall()
        # `payload` is already canonical (sorted-key JSON) and the ORDER BY pins row order, so the
        # serialization — hence the digest — is stable across stores and insertion order.
        blob = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def export_parquet(self, path: str | Path) -> None:
        """Export the whole append-only log to Parquet (the cold-storage format)."""

        self._con.execute("COPY readings TO ? (FORMAT PARQUET)", [str(path)])

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
