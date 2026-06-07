"""Surfacing the cross-source price divergence flag into the dashboard item.

When the live reconciliation can't agree two same-day NAVs, it keeps the value but tags the reading
``payload["divergence"]`` (P0/P1). The morning artifact must *show* that — tag-and-keep, not
silently drop — so the reviewer sees the value is unreconciled. Fixtures are single-sourced and
never diverge, so the offline artifact is unchanged; this pins the live path's flagged read.
"""

import pytest

from factor_scope import pipeline
from factor_scope.config import Config
from factor_scope.ingest import gather_fixture_readings
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.integration


def test_build_items_surfaces_a_price_divergence() -> None:
    with DuckDBStore() as store:
        store.append(gather_fixture_readings(Config(), as_of="2026-06-05"))
        # Override one held fund's latest NAV with a divergence-flagged read (a later as_of wins).
        store.append(
            [
                Reading(
                    series="prices",
                    key="561010",
                    as_of="2026-06-06",
                    fetched_at="t",
                    payload={"nav": 1.92, "source": "akshare", "divergence": 2.50},
                )
            ]
        )
        items = dict(pipeline._build_items(store, "2026-06-06"))

    flagged = items["561010"]
    by_src = {e.src: e for e in flagged.evidence}
    assert "prices:unreconciled" in by_src  # the machine-readable quality tag is present
    one_line = by_src["prices:unreconciled"].one_line
    assert "2.5" in one_line and "1.92" in one_line  # both the kept value and the peer are shown

    clean = items["515880"]  # a corroborated fund carries no such flag
    assert "prices:unreconciled" not in {e.src for e in clean.evidence}
