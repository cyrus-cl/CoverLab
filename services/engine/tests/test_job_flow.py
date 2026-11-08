from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient


def _create_project(client: TestClient, tmp_path: Path, payload: bytes = b"a" * 10) -> dict:
    source = tmp_path / "job source.wav"
    source.write_bytes(payload)
    response = client.post(
        "/api/v1/projects",
        json={"sourcePath": str(source), "outputDir": str(tmp_path / "output")},
    )
    assert response.status_code == 201
    return response.json()


def _wait_for_terminal_job(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/analyses/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"succeeded", "failed", "canceled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("analysis job did not reach a terminal state")


def test_analysis_api_runs_a_real_fingerprint_job(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = _create_project(client, tmp_path)

    create_response = client.post(f"/api/v1/projects/{project['id']}/analyses")

    assert create_response.status_code == 201
    completed = _wait_for_terminal_job(client, create_response.json()["id"])
    assert completed["status"] == "succeeded"
    assert completed["progressPercent"] == 100
    project_response = client.get(f"/api/v1/projects/{project['id']}")
    assert project_response.json()["sourceSha256"] is not None


def test_latest_analysis_can_be_recovered_from_a_persisted_project(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = _create_project(client, tmp_path, payload=b"resume" * 1024)
    created = client.post(f"/api/v1/projects/{project['id']}/analyses").json()

    response = client.get(f"/api/v1/projects/{project['id']}/analyses/latest")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["projectId"] == project["id"]


def test_latest_analysis_returns_stable_not_found_for_new_project(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = _create_project(client, tmp_path)

    response = client.get(f"/api/v1/projects/{project['id']}/analyses/latest")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "analysis_not_found"


def test_queued_analysis_can_be_canceled_and_retried(
    client: TestClient,
    tmp_path: Path,
) -> None:
    project = _create_project(client, tmp_path, payload=b"a" * (4 * 1024 * 1024))
    created = client.post(f"/api/v1/projects/{project['id']}/analyses").json()

    cancel_response = client.post(f"/api/v1/analyses/{created['id']}/cancel")

    assert cancel_response.status_code == 200
    canceled = _wait_for_terminal_job(client, created["id"])
    assert canceled["status"] == "canceled"
    retry_response = client.post(f"/api/v1/analyses/{created['id']}/retry")
    assert retry_response.status_code == 200
    assert _wait_for_terminal_job(client, created["id"])["status"] == "succeeded"
