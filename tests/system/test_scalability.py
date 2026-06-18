"""Scalability guardrails — a multi-year synthetic history that locks in bounded growth.

The nightly job runs every night for years, so two properties have to hold as the history scales,
not merely at the handful of nights the per-layer tests use:

* **Write path (ingest).** Re-pulling an unchanged snapshot writes nothing, and the append-only log
  grows *linearly* in the distinct facts — doubling the history doubles the rows, never squares
  them. This is content-dedup turning the nightly re-pull from quadratic to linear.
* **Read path (serving).** Listing the index, pointing at ``latest``, and charting a fund's trail
  each touch a *bounded* number of artifacts — independent of how long the history has grown. A
  regression to the old scan-every-night index would read N files instead of one.

The guardrail is the **work** each operation does (rows written, files opened), not wall-clock time:
that is the deterministic root cause of latency, and it keeps the assertion exact and offline. A
regression that re-introduces O(N) work fails here in CI instead of degrading in production.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from factor_scope.contract import Band, Dashboard, SeriesFactor, SeriesPoint
from factor_scope.store import DuckDBStore, Reading

pytestmark = pytest.mark.perf


def _nights(n: int, start: date = date(2016, 1, 1)) -> list[str]:
    """``n`` distinct ISO night-dates, oldest first — a synthetic multi-year history."""

    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _dash(as_of: str) -> Dashboard:
    return Dashboard(as_of=as_of, generated_at=f"{as_of}T22:00:00Z", snapshot_id=f"snap-{as_of}")


def _snapshot(nights: list[str], funds: list[str], fetched_at: str) -> list[Reading]:
    """One NAV bar per fund per night — the full per-fund history a nightly run re-pulls."""

    return [
        Reading(
            series="prices",
            key=fund,
            as_of=as_of,
            fetched_at=fetched_at,
            payload={"nav": 1.0 + n},
        )
        for n, as_of in enumerate(nights)
        for fund in funds
    ]


def test_store_growth_is_linear_and_resnapshotting_writes_nothing() -> None:
    # The headline write-path guarantee: as the history scales, the append-only log grows in the
    # distinct facts it holds — not in the nightly re-pulls of them.
    funds = ["F0", "F1", "F2", "F3", "F4"]
    one_year = _nights(120)
    two_years = _nights(240)

    small = DuckDBStore(":memory:")
    large = DuckDBStore(":memory:")
    try:
        small.append(_snapshot(one_year, funds, "ingest-1"))
        large.append(_snapshot(two_years, funds, "ingest-1"))

        # Linear, not quadratic: doubling the nights doubles the rows (a store that re-stored the
        # whole history each night would hold ~N²/2 × funds instead).
        assert small.count("prices") == len(one_year) * len(funds)
        assert large.count("prices") == len(two_years) * len(funds)
        assert large.count("prices") == 2 * small.count("prices")

        # A second ingest over the unchanged snapshot — every bar re-pulled under a fresh
        # fetched_at, exactly as the next night's run does — is a pure no-op under content-dedup,
        # so the log never amplifies no matter how many nights re-pull it.
        assert large.append(_snapshot(two_years, funds, "ingest-2")) == 0
        assert large.count("prices") == len(two_years) * len(funds)
    finally:
        small.close()
        large.close()


def test_index_and_latest_serving_stay_flat_as_history_grows(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastapi", reason="the serve extra is not installed")
    from fastapi.testclient import TestClient

    from factor_scope.history import record
    from factor_scope.serve import create_app

    history = tmp_path / "dashboards"
    nights = _nights(750)  # ~3 years of nights
    for as_of in nights:
        record(_dash(as_of), history)
    client = TestClient(create_app(history))

    opened: list[str] = []
    real_read_text = Path.read_text

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        opened.append(self.name)
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    # `/dashboards` answers from the materialized catalog — one file read, never the 750 per-night
    # artifacts — so the index stays O(1) as the history grows. The response itself is a bounded
    # page (R5), but the whole multi-year history is catalogued behind it (X-Total-Count).
    opened.clear()
    index = client.get("/dashboards", params={"limit": 500})
    assert index.status_code == 200
    assert index.headers["x-total-count"] == str(len(nights))
    assert [e["as_of"] for e in index.json()["entries"]] == nights[:500]
    assert opened == ["index.json"]

    # `/dashboards/latest` is a pointer to the last catalog entry plus that one night — two reads,
    # not a scan, however long the history is.
    opened.clear()
    newest = client.get("/dashboards/latest")
    assert newest.status_code == 200 and newest.json()["as_of"] == nights[-1]
    assert opened == ["index.json", f"{nights[-1]}.json"]


def test_a_fund_trail_serves_flat_as_the_trail_grows_over_years(tmp_path, monkeypatch) -> None:
    pytest.importorskip("fastapi", reason="the serve extra is not installed")
    from fastapi.testclient import TestClient

    from factor_scope import series
    from factor_scope.serve import create_app

    series_dir = tmp_path / "series"
    nights = _nights(750)  # a multi-year trail — one point appended per night
    for as_of in nights:
        point = SeriesPoint(
            as_of=as_of,
            nav=1.0,
            gain=0.1,
            factors=[SeriesFactor(factor="trend", level=Band.HIGH)],
        )
        series.record([("513100", "GF Nasdaq", point)], series_dir)
    client = TestClient(create_app(tmp_path / "dashboards", series_dir=series_dir))

    opened: list[str] = []
    real_read_text = Path.read_text

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        opened.append(self.name)
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    # Charting a fund reads only its own trail artifact — one file, whatever its length — so the
    # time-series endpoint stays flat as the trail (and the store behind it) grows over the years.
    opened.clear()
    resp = client.get("/series/513100")
    assert resp.status_code == 200
    assert [p["as_of"] for p in resp.json()["points"]] == nights
    assert opened == ["513100.json"]
