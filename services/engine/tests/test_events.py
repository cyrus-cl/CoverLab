from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient


def test_sse_replays_persisted_events_after_last_event_id(
    client: TestClient,
    tmp_path: Path,
) -> None:
    started = datetime.now(UTC)
    source = tmp_path / "events.wav"
    source.write_bytes(b"event-source")
    project = client.post(
        "/api/v1/projects",
        json={"sourcePath": str(source), "outputDir": str(tmp_path / "output")},
    ).json()
    job = client.post(f"/api/v1/projects/{project['id']}/analyses").json()

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = client.get(f"/api/v1/analyses/{job['id']}").json()
        if current["status"] == "succeeded":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("analysis did not finish")

    all_events = client.get(f"/api/v1/analyses/{job['id']}/events")
    assert all_events.status_code == 200
    assert all_events.headers["content-type"].startswith("text/event-stream")
    assert "event: JOB_QUEUED" in all_events.text
    assert "event: FINGERPRINT_SUCCEEDED" in all_events.text

    first_id_line = next(line for line in all_events.text.splitlines() if line.startswith("id: "))
    first_id = first_id_line.removeprefix("id: ")
    replay = client.get(
        f"/api/v1/analyses/{job['id']}/events",
        headers={"Last-Event-ID": first_id},
    )

    assert f"id: {first_id}\n" not in replay.text
    assert "event: FINGERPRINT_SUCCEEDED" in replay.text
    persisted_events = [
        json.loads(line.removeprefix("data: "))
        for line in replay.text.splitlines()
        if line.startswith("data: ")
    ]
    assert persisted_events
    finished = datetime.now(UTC)
    for event in persisted_events:
        timestamp = datetime.fromisoformat(event["createdAt"])
        assert timestamp.utcoffset() == timedelta(0)
        assert started <= timestamp <= finished
