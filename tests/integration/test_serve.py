"""Integration tests for the read-only history API — the seam a frontend consumes."""

import pytest

pytest.importorskip("fastapi", reason="the serve extra is not installed")

from fastapi.testclient import TestClient  # noqa: E402

from factor_scope.contract import Dashboard  # noqa: E402
from factor_scope.history import record  # noqa: E402
from factor_scope.serve import create_app  # noqa: E402

pytestmark = pytest.mark.integration


def _dash(as_of: str) -> Dashboard:
    return Dashboard(as_of=as_of, generated_at=f"{as_of}T22:00:00Z", snapshot_id=f"snap-{as_of}")


@pytest.fixture()
def client(tmp_path) -> TestClient:
    record(_dash("2026-06-04"), tmp_path)
    record(_dash("2026-06-05"), tmp_path)
    return TestClient(create_app(tmp_path))


def test_index_lists_the_recorded_nights(client: TestClient) -> None:
    resp = client.get("/dashboards")
    assert resp.status_code == 200
    assert [e["as_of"] for e in resp.json()["entries"]] == ["2026-06-04", "2026-06-05"]
    # The index moves nightly — clients must revalidate.
    assert resp.headers["cache-control"] == "no-cache"


def test_a_night_is_served_as_the_recorded_artifact(client: TestClient) -> None:
    resp = client.get("/dashboards/2026-06-04")
    assert resp.status_code == 200
    dash = Dashboard.model_validate(resp.json())
    assert dash == _dash("2026-06-04")
    # A recorded night never changes, so the response is cacheable forever.
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_latest_is_the_newest_night(client: TestClient) -> None:
    resp = client.get("/dashboards/latest")
    assert resp.status_code == 200
    assert resp.json()["as_of"] == "2026-06-05"
    assert resp.headers["cache-control"] == "no-cache"


def test_unknown_or_malformed_dates_are_absent(client: TestClient) -> None:
    assert client.get("/dashboards/2026-06-01").status_code == 404
    # The date IS the filename; anything not date-shaped (traversal included) is foreclosed.
    assert client.get("/dashboards/not-a-date").status_code == 404
    assert client.get("/dashboards/..%2Findex").status_code == 404


def test_an_empty_history_serves_an_empty_index_and_no_latest(tmp_path) -> None:
    bare = TestClient(create_app(tmp_path / "absent"))
    assert bare.get("/dashboards").json() == {"schema_version": 1, "entries": []}
    assert bare.get("/dashboards/latest").status_code == 404


def test_openapi_exposes_the_contract_models(client: TestClient) -> None:
    # The typed schema a frontend client is generated from — the contract models ARE the API.
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "Dashboard" in schemas
    assert "DashboardIndex" in schemas
