"""Themes adapter — candidate industries for the emerging funnel's Stage A.

`themes.csv → {theme, as_of, acceleration, base_level, breadth, crowding, broad_adoption,
path_to_profit, fad_resistant, lead_chain, wrapper_exists, constituents}`. Each row is one
industry's dated weak-signal read plus its reference constituents (the ``;``-separated names of its
index, the seed the theme→fund mapping infers candidate funds from). Keyed by theme name and
stamped with its own research ``as_of`` so reads stay point-in-time. Live theme discovery is a
clustering/tagging pass (BERTopic / an LLM tag) over a text stream — never wired into CI;
only the fixture backend runs offline.
"""

from __future__ import annotations

from pathlib import Path

from factor_scope.ingest.base import as_float, read_rows, required_str
from factor_scope.store import Reading

SERIES = "themes"
FIXTURE = "themes.csv"
_REQUIRED = (
    "theme",
    "as_of",
    "acceleration",
    "base_level",
    "breadth",
    "crowding",
    "broad_adoption",
    "path_to_profit",
    "fad_resistant",
    "lead_chain",
    "wrapper_exists",
    "constituents",
)
_BOOL_FIELDS = ("broad_adoption", "path_to_profit", "fad_resistant", "lead_chain", "wrapper_exists")


def _constituents(row: dict[str, str]) -> list[str]:
    """The ``;``-separated reference constituents → a clean list (blank entries dropped)."""

    return [name.strip() for name in (row.get("constituents") or "").split(";") if name.strip()]


def _as_bool(row: dict[str, str], field: str, line_no: int) -> bool:
    """A 0/1 (or true/false) flag column → bool, via the numeric helper for a uniform error."""

    return as_float(row, field, line_no, SERIES) != 0.0


def parse(text: str, *, fetched_at: str) -> list[Reading]:
    readings: list[Reading] = []
    for line_no, row in read_rows(text, _REQUIRED, SERIES):
        theme = required_str(row, "theme", line_no, SERIES)
        as_of = required_str(row, "as_of", line_no, SERIES)
        payload: dict[str, object] = {
            "acceleration": as_float(row, "acceleration", line_no, SERIES),
            "base_level": as_float(row, "base_level", line_no, SERIES),
            "breadth": int(as_float(row, "breadth", line_no, SERIES)),
            "crowding": as_float(row, "crowding", line_no, SERIES),
            "constituents": _constituents(row),
        }
        for field in _BOOL_FIELDS:
            payload[field] = _as_bool(row, field, line_no)
        readings.append(
            Reading(series=SERIES, key=theme, as_of=as_of, fetched_at=fetched_at, payload=payload)
        )
    return readings


def load_fixture(path: Path, *, fetched_at: str) -> list[Reading]:
    return parse(path.read_text(encoding="utf-8"), fetched_at=fetched_at)
