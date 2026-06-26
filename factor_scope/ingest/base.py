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
from datetime import UTC, date, datetime, timedelta
from typing import Any


class IngestError(ValueError):
    """A source row could not be parsed into a Reading."""


def day_after(since: str) -> date:
    """The day after an incremental watermark — the first session a re-pull should request.

    The per-fund time series are watermarked: a re-pull asks the source for only sessions strictly
    newer than the latest one stored, so the multi-year history is fetched once and each later night
    pulls only the new bars. Adapters format this for their own backend (AkShare/EastMoney want
    ``YYYYMMDD``, Baostock wants ``YYYY-MM-DD``).
    """

    return date.fromisoformat(since) + timedelta(days=1)


def spot_date(value: Any) -> str:
    """A spot-board session date as ISO ``YYYY-MM-DD`` — it arrives as a pandas Timestamp.

    Used by the board normalizer (:func:`factor_scope.ingest.etf_scale._board_row`) to ISO-stamp the
    shared spot board's session date once at its edge, so every downstream leg reads a domain date.
    """

    if hasattr(value, "strftime"):
        return str(value.strftime("%Y-%m-%d"))
    return str(value)[:10]


def run_date(fetched_at: str) -> date | None:
    """The calendar date of a live ``fetched_at`` stamp, or ``None`` if it is not a real date.

    Live pulls stamp the wall-clock instant; the cold-start seed window is measured back from this
    run date. Unit fakes pass a sentinel (``"t"``) rather than a timestamp — those degrade to no
    bounded seed (the adapter falls back to its backend's default range) rather than raising.
    """

    try:
        return date.fromisoformat(fetched_at[:10])
    except ValueError:
        return None


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


class _HostBreaker:
    """A run-scoped circuit breaker: once a host refuses N times in a row, callers skip it.

    The EastMoney K-line host blocks the IP under a sustained burst and stays blocked for a long,
    undocumented cooldown; once it starts refusing there is no point spending three retries × the
    wall-clock deadline on every remaining fund. After ``threshold`` consecutive failures the host
    is *open* and the price/activity adapters route straight to their fallback (Sina / spot board)
    for the rest of the run; a single success closes it (the cooldown may pass mid-run). State is
    per run — :meth:`reset` is called at the top of each market gather.
    """

    def __init__(self, threshold: int = 5) -> None:
        self._threshold = threshold
        self._consecutive: dict[str, int] = {}
        self._failures: dict[str, int] = {}
        self._open: set[str] = set()

    def is_open(self, host: str) -> bool:
        """Has ``host`` tripped open — should callers skip it and go straight to the fallback?"""

        return host in self._open

    def record_success(self, host: str) -> None:
        """A reachable host: clear its failure streak and close it back."""

        self._consecutive[host] = 0
        self._open.discard(host)

    def record_failure(self, host: str) -> None:
        """A refusal: extend the streak (and the run total), tripping open at the threshold."""

        self._failures[host] = self._failures.get(host, 0) + 1
        streak = self._consecutive.get(host, 0) + 1
        self._consecutive[host] = streak
        if streak >= self._threshold:
            self._open.add(host)

    def failures(self, host: str) -> int:
        """Total refusals from ``host`` this run — the run-level alarm's count."""

        return self._failures.get(host, 0)

    def reset(self) -> None:
        """Clear all run-scoped state — called at the top of each market gather."""

        self._consecutive.clear()
        self._failures.clear()
        self._open.clear()


# The per-fund K-line host the impersonating :mod:`eastmoney` client contacts (prices + trading
# activity). One shared breaker spans both legs because they hit the same IP — a block on one is a
# block on both.
EASTMONEY_KLINE = "push2his.eastmoney.com"
host_breaker = _HostBreaker()


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
