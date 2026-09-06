"""Health, metadata and error-contract tests.

These run without a database on purpose: ``/health`` degrades to ``db: "down"``
rather than raising, so unit CI is not coupled to Postgres.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.request_context import REQUEST_ID_HEADER


def test_health_returns_200_without_a_database(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["demo_mode"] is True
    assert body["app_env"] == "test"
    assert body["db"] in {"up", "down"}
    assert body["status"] == ("ok" if body["db"] == "up" else "degraded")


def test_health_echoes_a_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers[REQUEST_ID_HEADER]


def test_health_adopts_a_supplied_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "demo-run-042"})

    assert response.headers[REQUEST_ID_HEADER] == "demo-run-042"


def test_health_rejects_a_malformed_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "bad id with spaces"})

    assert response.headers[REQUEST_ID_HEADER] != "bad id with spaces"


def test_meta_route_is_mounted_under_v1(client: TestClient) -> None:
    response = client.get("/v1/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["app_name"] == "NADDP API"
    assert body["api_prefix"] == "/v1"
    assert body["demo_mode"] is True
    assert body["synthetic_data"] is True
    assert "consular" in body["bounded_contexts"]
    assert "anthropic_api_key" not in body


def test_unknown_route_returns_problem_details(client: TestClient) -> None:
    response = client.get("/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["code"] == "http_404"
    assert body["request_id"]


def test_openapi_document_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "NADDP API"
    assert "/v1/meta" in schema["paths"]


@pytest.mark.integration
def test_health_reports_db_up_against_a_live_database(
    client: TestClient,
    database_available: bool,
) -> None:
    if not database_available:
        pytest.skip("No database reachable; start it with `docker compose up -d db`.")

    body = client.get("/health").json()
    assert body["db"] == "up"
    assert body["status"] == "ok"
