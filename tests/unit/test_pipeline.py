"""The run-date resolution seam — what point-in-time date a run reasons on.

Offline (fixtures) the date is the committed manifest stamp, so a fixtures run stays byte-for-byte;
online (live) it defaults to the run date, because the live store is a moving, append-only log whose
point-in-time ceiling must advance to today. An explicit ``--as-of`` always wins on either source.
"""

from datetime import date

import pytest

from factor_scope.config import Config
from factor_scope.pipeline import _resolve_as_of

pytestmark = pytest.mark.unit


def test_live_with_no_override_defaults_to_the_run_date() -> None:
    as_of = _resolve_as_of(Config(source="live"), today=lambda: date(2026, 6, 19))
    assert as_of == "2026-06-19"  # the run date, not the fixtures manifest stamp


def test_fixtures_with_no_override_uses_the_manifest_stamp() -> None:
    # The committed snapshot's stamp — deterministic, so a fixtures run reproduces byte-for-byte.
    as_of = _resolve_as_of(Config(source="fixtures"), today=lambda: date(2026, 6, 19))
    assert as_of == "2026-06-05"


def test_an_explicit_as_of_wins_on_either_source() -> None:
    # The clock is never consulted when the operator pinned a date.
    def _clock() -> date:
        raise AssertionError("today() must not be called when as_of is given")

    assert _resolve_as_of(Config(source="live", as_of="2026-01-02"), today=_clock) == "2026-01-02"
    assert (
        _resolve_as_of(Config(source="fixtures", as_of="2026-01-02"), today=_clock) == "2026-01-02"
    )
