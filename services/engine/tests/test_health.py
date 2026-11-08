from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_requires_the_launch_token(client_without_token: TestClient) -> None:
    response = client_without_token.get("/api/v1/health/live")

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "invalid_local_token",
            "message": "Local engine access denied",
        }
    }


def test_health_accepts_the_launch_token(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "local"}
