from __future__ import annotations

from fastapi.testclient import TestClient


def test_untrusted_origin_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403


def test_allowed_origin_receives_cors_headers(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health/live",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-AI4Music-Token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
