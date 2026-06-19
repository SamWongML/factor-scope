"""Shared helpers for the ingestion adapters.

Each adapter turns one source into stamped :class:`~factor_scope.store.Reading` rows. Every adapter
has a **live backend** (``fetch_live``, the default; lazily imports its heavy dependency so the core
installs and CI run without it) and a **fixture backend** (``load_fixture``, the offline test mode,
deterministic). Malformed source rows raise :class:`IngestError` — missing is not
the same as bad, but unparseable is a hard error.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime


class IngestError(ValueError):
    """A source row could not be parsed into a Reading."""


def fetched_at_for(as_of: str) -> str:
    """A deterministic ``fetched_at`` stamp for fixtures — derived from ``as_of``, never the clock.

    This keeps a fixtures ingest reproducible so the downstream artifact is byte-for-byte stable.
    """

    return f"{as_of}T22:00:00Z"


def fetched_at_now() -> str:
    """The real wall-clock instant of a *live* pull — telemetry, never the artifact's clock.

    A live read records when it was actually fetched (the live counterpart to the fixtures-only
    :func:`fetched_at_for`); ``fetched_at`` never reaches ``dashboard.json``, so this stays off the
    determinism path. The append-only store's content-addressed dedup keys on payload, not this
    stamp, so a same-day re-pull of unchanged facts is still a no-op.
    """

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_rows(
    text: str, required: tuple[str, ...], source: str
) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield ``(line_number, row)`` for a CSV, validating the header has ``required`` columns."""

    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames
    if header is None or set(required) - set(header):
        raise IngestError(f"{source}: header must contain {required}; got {header}")
    yield from enumerate(reader, start=2)  # line 1 is the header


def as_float(row: dict[str, str], field: str, line_no: int, source: str) -> float:
    raw = (row.get(field) or "").strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise IngestError(f"{source} line {line_no}: {field}={raw!r} is not a number") from exc


def required_str(row: dict[str, str], field: str, line_no: int, source: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise IngestError(f"{source} line {line_no}: {field} is empty")
    return value


def optional_float(row: dict[str, str], field: str, line_no: int, source: str) -> float | None:
    """A numeric field that may be absent — empty degrades to ``None``; non-empty must parse."""

    if not (row.get(field) or "").strip():
        return None
    return as_float(row, field, line_no, source)
