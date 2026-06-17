"""Integration tests for the read-only history API — the seam a frontend consumes."""

import pytest

pytest.importorskip("fastapi", reason="the serve extra is not installed")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from factor_scope.cli import app as cli  # noqa: E402
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
    # The index moves only nightly — cacheable with revalidation, carrying a strong ETag.
    assert resp.headers["cache-control"] == "public, max-age=0, must-revalidate"
    assert resp.headers["etag"]


def test_index_revalidates_to_304_on_a_matching_etag(client: TestClient) -> None:
    first = client.get("/dashboards")
    etag = first.headers["etag"]
    again = client.get("/dashboards", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag


def test_a_night_is_served_as_the_recorded_artifact(client: TestClient) -> None:
    resp = client.get("/dashboards/2026-06-04")
    assert resp.status_code == 200
    dash = Dashboard.model_validate(resp.json())
    assert dash == _dash("2026-06-04")
    # A recorded night never changes, so the response is cacheable forever, with a strong ETag
    # for the static/CDN tier to validate against.
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resp.headers["etag"]


def test_a_night_revalidates_to_304_on_a_matching_etag(client: TestClient) -> None:
    first = client.get("/dashboards/2026-06-04")
    etag = first.headers["etag"]
    again = client.get("/dashboards/2026-06-04", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag
    assert again.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_latest_is_the_newest_night(client: TestClient) -> None:
    resp = client.get("/dashboards/latest")
    assert resp.status_code == 200
    assert resp.json()["as_of"] == "2026-06-05"
    # `latest` is a pointer to the last catalog entry — revalidated, not re-scanned.
    assert resp.headers["cache-control"] == "public, max-age=0, must-revalidate"
    assert resp.headers["etag"]


def test_latest_revalidates_to_304_on_a_matching_etag(client: TestClient) -> None:
    first = client.get("/dashboards/latest")
    etag = first.headers["etag"]
    again = client.get("/dashboards/latest", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag


def test_unknown_or_malformed_dates_are_absent(client: TestClient) -> None:
    assert client.get("/dashboards/2026-06-01").status_code == 404
    # The date IS the filename; anything not date-shaped (traversal included) is foreclosed.
    assert client.get("/dashboards/not-a-date").status_code == 404
    assert client.get("/dashboards/..%2Findex").status_code == 404


def test_an_empty_history_serves_an_empty_index_and_no_latest(tmp_path) -> None:
    bare = TestClient(create_app(tmp_path / "absent"))
    assert bare.get("/dashboards").json() == {"schema_version": 1, "entries": []}
    assert bare.get("/dashboards/latest").status_code == 404


def test_the_index_is_paginated_and_bounded(tmp_path) -> None:
    for day in range(1, 7):
        record(_dash(f"2026-06-{day:02d}"), tmp_path)
    client = TestClient(create_app(tmp_path))

    page = client.get("/dashboards", params={"limit": 2, "offset": 2})
    assert page.status_code == 200
    # A bounded window into the oldest-first index, with the full count out of band.
    assert [e["as_of"] for e in page.json()["entries"]] == ["2026-06-03", "2026-06-04"]
    assert page.headers["x-total-count"] == "6"
    # Link relations let a client walk the whole history without an unbounded response.
    assert 'rel="next"' in page.headers["link"]
    assert 'rel="prev"' in page.headers["link"]

    last = client.get("/dashboards", params={"limit": 2, "offset": 4})
    assert [e["as_of"] for e in last.json()["entries"]] == ["2026-06-05", "2026-06-06"]
    assert 'rel="next"' not in last.headers.get("link", "")

    # The window bounds the payload regardless of how long the history grows.
    over = client.get("/dashboards", params={"limit": 9999})
    assert over.status_code == 422


def test_large_list_responses_are_compressed(tmp_path) -> None:
    for day in range(1, 21):
        record(_dash(f"2026-06-{day:02d}"), tmp_path)
    client = TestClient(create_app(tmp_path))
    resp = client.get("/dashboards", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"


def test_cors_is_closed_by_default_and_configurable(tmp_path) -> None:
    record(_dash("2026-06-05"), tmp_path)
    cross = {"Origin": "https://app.example", "Access-Control-Request-Method": "GET"}

    closed = TestClient(create_app(tmp_path))
    assert "access-control-allow-origin" not in closed.get("/dashboards", headers=cross).headers

    open_to = TestClient(create_app(tmp_path, allow_origins=("https://app.example",)))
    allowed = open_to.get("/dashboards", headers=cross)
    assert allowed.headers["access-control-allow-origin"] == "https://app.example"


def test_a_closed_surface_rejects_the_cors_preflight(tmp_path) -> None:
    # The browser's pre-flight OPTIONS for a cross-origin read is not granted on a closed surface.
    record(_dash("2026-06-05"), tmp_path)
    preflight = {"Origin": "https://app.example", "Access-Control-Request-Method": "GET"}

    closed = TestClient(create_app(tmp_path)).options("/dashboards", headers=preflight)
    assert "access-control-allow-origin" not in closed.headers

    open_to = TestClient(create_app(tmp_path, allow_origins=("https://app.example",)))
    granted = open_to.options("/dashboards", headers=preflight)
    assert granted.headers["access-control-allow-origin"] == "https://app.example"


def test_openapi_exposes_the_contract_models(client: TestClient) -> None:
    # The typed schema a frontend client is generated from — the contract models ARE the API.
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "Dashboard" in schemas
    assert "DashboardIndex" in schemas


def test_serve_command_binds_the_history_api(tmp_path, monkeypatch) -> None:
    # The `serve` entrypoint builds the read-only app over the given history and hands it to
    # uvicorn on the requested host/port — exercised without standing up a real server.
    record(_dash("2026-06-05"), tmp_path)
    bound: dict[str, object] = {}

    def fake_run(built: FastAPI, host: str, port: int) -> None:
        bound["host"], bound["port"] = host, port
        listed = TestClient(built).get("/dashboards").json()["entries"]
        bound["nights"] = [e["as_of"] for e in listed]

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(cli, ["serve", "--history-dir", str(tmp_path), "--port", "9999"])

    assert result.exit_code == 0, result.output
    assert bound == {"host": "127.0.0.1", "port": 9999, "nights": ["2026-06-05"]}


def test_serve_does_not_open_cors_wide_on_a_remote_bind(tmp_path, monkeypatch) -> None:
    # Binding beyond localhost without an explicit allow-list must NOT echo arbitrary origins.
    record(_dash("2026-06-05"), tmp_path)
    cross = {"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    seen: dict[str, object] = {}

    def fake_run(built: FastAPI, host: str, port: int) -> None:
        headers = TestClient(built).get("/dashboards", headers=cross).headers
        seen["acao"] = headers.get("access-control-allow-origin")

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(cli, ["serve", "--history-dir", str(tmp_path), "--host", "0.0.0.0"])

    assert result.exit_code == 0, result.output
    assert seen["acao"] is None


def test_serve_honours_an_explicit_cors_allow_list(tmp_path, monkeypatch) -> None:
    record(_dash("2026-06-05"), tmp_path)
    cross = {"Origin": "https://app.example", "Access-Control-Request-Method": "GET"}
    seen: dict[str, object] = {}

    def fake_run(built: FastAPI, host: str, port: int) -> None:
        headers = TestClient(built).get("/dashboards", headers=cross).headers
        seen["acao"] = headers.get("access-control-allow-origin")

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(
        cli,
        ["serve", "--history-dir", str(tmp_path), "--host", "0.0.0.0",
         "--allow-origin", "https://app.example"],
    )

    assert result.exit_code == 0, result.output
    assert seen["acao"] == "https://app.example"
