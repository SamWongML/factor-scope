"""Positions adapter — the user's book (`positions.csv → {code, name, cost_basis, shares, list}`).

This is the simplest point-in-time source and the seed of the three dashboard lists. No personal
wealth-marketplace API exists (理财通/蚂蚁财富 expose none — spec §04), so the file *is* the source;
the engine stamps it as known-as-of the run date and computes per-item gain from cost basis + NAV.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.contract import ListName
from factor_scope.ingest.base import IngestError, as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "positions"
FIXTURE = "positions.csv"
_REQUIRED = ("code", "name", "cost_basis", "shares", "list")


def parse(text: str, *, as_of: str, fetched_at: str) -> list[Reading]:
    """Parse a `positions.csv` body into readings stamped with the run's ``as_of``."""

    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        code = required_str(row, "code", line_no, SERIES)
        name = required_str(row, "name", line_no, SERIES)
        cost_basis = as_float(row, "cost_basis", line_no, SERIES)
        shares = as_float(row, "shares", line_no, SERIES)
        raw_list = required_str(row, "list", line_no, SERIES)
        try:
            list_name = ListName(raw_list)
        except ValueError as exc:
            raise IngestError(
                f"{SERIES} line {line_no}: list={raw_list!r} is not one of "
                f"{[m.value for m in ListName]}"
            ) from exc
        readings.append(
            Reading(
                series=SERIES,
                key=code,
                as_of=as_of,
                fetched_at=fetched_at,
                payload={
                    "name": name,
                    "cost_basis": cost_basis,
                    "shares": shares,
                    "list": list_name.value,
                },
            )
        )
    return readings


def load_fixture(path: Path, *, as_of: str, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), as_of=as_of, fetched_at=fetched_at)
