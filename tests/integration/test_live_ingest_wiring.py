"""The ``--live`` ingest path refreshes holdings, not just prices + the macro dial.

These stay offline by stubbing each adapter's heavy ``fetch_live`` (the real bodies hit the
network and live behind ``FACTOR_SCOPE_LIVE=1`` in ``test_adapters_live.py``). They pin the wiring
of ``gather_live_readings``: every held fund's holdings are refreshed (so the connection graph
rebuilds from live disclosures) and each configured EDGAR filer is pulled.
"""

import pytest

from factor_scope.config import Config
from factor_scope.ingest import baostock, edgar, fred, fund_holdings, gather_live_readings, prices
from factor_scope.ingest.base import IngestError
from factor_scope.store import Reading

pytestmark = pytest.mark.integration


def _stub_adapters(monkeypatch) -> None:
    monkeypatch.setattr(
        prices,
        "fetch_live",
        lambda key, *, fetched_at: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.0})
        ],
    )
    monkeypatch.setattr(
        baostock,
        "fetch_live",
        lambda key, *, fetched_at: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 1.0})  # corroborates the AkShare read
        ],
    )
    monkeypatch.setattr(
        fund_holdings,
        "fetch_live",
        lambda fund, *, fetched_at: [
            Reading(series="fund_holdings", key=f"{fund}/X", as_of="2026-03-31",
                    fetched_at=fetched_at, payload={"fund": fund, "holding": "X", "weight": 0.1})
        ],
    )
    monkeypatch.setattr(
        edgar,
        "fetch_live",
        lambda cik, *, form="13F-HR", fetched_at: [
            Reading(series="edgar", key=f"{cik}/COHR", as_of="2026-03-31", fetched_at=fetched_at,
                    payload={"filer": cik, "holding": "COHR", "weight": 0.05, "form": form})
        ],
    )
    monkeypatch.setattr(fred, "fetch_live", lambda series_id, *, fetched_at: [])


def test_gather_live_refreshes_fund_holdings_per_held_fund(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    readings = gather_live_readings(Config(source="live"), as_of="2026-06-05")

    held = [r for r in readings if r.series == "positions"]
    refreshed = [r for r in readings if r.series == "fund_holdings"]
    assert held  # the offline positions fixture is the universe
    # one live holdings refresh per held fund — so the graph rebuilds from live disclosures
    assert {r.payload["fund"] for r in refreshed} == {r.key for r in held}


def test_gather_live_pulls_each_configured_edgar_filer(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    config = Config(source="live", edgar_ciks=("0001067983", "0000102909"))
    readings = gather_live_readings(config, as_of="2026-06-05")

    edgar_rows = [r for r in readings if r.series == "edgar"]
    assert {r.payload["filer"] for r in edgar_rows} == {"0001067983", "0000102909"}
    # pulled as monthly N-PORT and weighted, so the holdings feed the look-through graph
    assert all(r.payload["form"] == "NPORT-P" for r in edgar_rows)
    assert all("weight" in r.payload for r in edgar_rows)


def test_gather_live_pulls_no_edgar_filers_by_default(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    readings = gather_live_readings(Config(source="live"), as_of="2026-06-05")
    assert not [r for r in readings if r.series == "edgar"]


def test_gather_live_corroborates_prices_across_sources(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    # AkShare and Baostock agree → one corroborated price per held fund (not one per source)
    readings = gather_live_readings(Config(source="live"), as_of="2026-06-05")
    held = {r.key for r in readings if r.series == "positions"}
    priced = [r for r in readings if r.series == "prices"]
    assert {r.key for r in priced} == held
    assert len(priced) == len(held)


def test_gather_live_surfaces_a_source_disagreement(monkeypatch) -> None:
    _stub_adapters(monkeypatch)
    # Baostock materially disagrees with AkShare → the cross-validation surfaces it, doesn't hide it
    monkeypatch.setattr(
        baostock,
        "fetch_live",
        lambda key, *, fetched_at: [
            Reading(series="prices", key=key, as_of="2026-06-05", fetched_at=fetched_at,
                    payload={"nav": 99.0})
        ],
    )
    with pytest.raises(IngestError, match="disagree"):
        gather_live_readings(Config(source="live"), as_of="2026-06-05")
