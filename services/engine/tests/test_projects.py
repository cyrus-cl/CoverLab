from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

ClientFactory = Callable[[], TestClient]


def test_create_project_references_source_without_copying(
    client: TestClient,
    tmp_path: Path,
) -> None:
    source = tmp_path / "示例 音频.wav"
    source.write_bytes(b"RIFF-local-source")
    before = source.read_bytes()

    response = client.post(
        "/api/v1/projects",
        json={"sourcePath": str(source), "outputDir": str(tmp_path / "out")},
    )

    assert response.status_code == 201
    assert Path(response.json()["sourcePath"]) == source.resolve()
    assert Path(response.json()["outputDir"]).is_dir()
    assert response.json()["name"] == "示例 音频"
    assert source.read_bytes() == before
    assert not (tmp_path / "out" / source.name).exists()


def test_create_project_rejects_missing_source(
    client: TestClient,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "out"

    response = client.post(
        "/api/v1/projects",
        json={
            "sourcePath": str(tmp_path / "missing.wav"),
            "outputDir": str(output_root),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "source_missing"
    assert not output_root.exists()


def test_project_list_survives_a_new_app_instance(
    client_factory: ClientFactory,
    tmp_path: Path,
) -> None:
    started = datetime.now(UTC)
    source = tmp_path / "persistent.wav"
    source.write_bytes(b"RIFF-persistent")
    output_root = tmp_path / "exports"

    with client_factory() as first:
        created_response = first.post(
            "/api/v1/projects",
            json={"sourcePath": str(source), "outputDir": str(output_root)},
        )
        assert created_response.status_code == 201
        created = created_response.json()

    with client_factory() as second:
        list_response = second.get("/api/v1/projects")
        get_response = second.get(f"/api/v1/projects/{created['id']}")

    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == created["id"]
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]
    finished = datetime.now(UTC)
    for project in [created, list_response.json()[0], get_response.json()]:
        for key in ["createdAt", "updatedAt", "lastOpenedAt"]:
            timestamp = datetime.fromisoformat(project[key])
            assert timestamp.utcoffset() == timedelta(0)
            assert started <= timestamp <= finished
