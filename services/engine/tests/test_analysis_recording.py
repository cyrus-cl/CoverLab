from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from ai4music_core.media.ffmpeg import FFmpegTools
from fastapi.testclient import TestClient


def project_audio(client: TestClient, tmp_path: Path) -> tuple[dict, Path]:
    source = tmp_path / "分析 测试.wav"
    rate = 16000
    samples = [int(14000 * math.exp(-((i % 8000) / 180))) for i in range(rate * 12)]
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    response = client.post(
        "/api/v1/projects",
        json={
            "sourcePath": str(source),
            "outputDir": str(tmp_path / "项目输出"),
        },
    )
    assert response.status_code == 201
    return response.json(), source


def terminal(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/analyses/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "canceled"}:
            return job
        time.sleep(0.025)
    raise AssertionError("analysis did not finish")


def test_analysis_publishes_real_waveform_and_beats_without_changing_source(client, tmp_path):
    project, source = project_audio(client, tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    response = client.post(
        f"/api/v1/projects/{project['id']}/process", json={"operation": "analyze"}
    )
    assert response.status_code == 201, response.text
    job = terminal(client, response.json()["id"])
    assert job["status"] == "succeeded", job
    assert [s["name"] for s in job["stages"]][-2:] == ["extract_waveform", "estimate_beats"]
    artifacts = client.get(f"/api/v1/projects/{project['id']}/artifacts").json()
    by_kind = {a["kind"]: a for a in artifacts}
    waveform = client.get(by_kind["waveform"]["contentUrl"]).json()
    beats = client.get(by_kind["beats"]["contentUrl"]).json()
    assert waveform["durationSeconds"] == pytest.approx(12, abs=0.1)
    assert 0 < len(waveform["peaks"]) <= 1600
    assert any(peak[1] > 0.1 for peak in waveform["peaks"])
    assert beats["bpm"] == pytest.approx(120, abs=4)
    assert len(beats["beats"]) > 10
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_lyrics_edits_create_valid_json_lrc_and_srt_and_reject_out_of_bounds(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    document = {
        "schemaVersion": 1,
        "durationSeconds": 12,
        "language": "zh",
        "segments": [
            {"start": 1.25, "end": 3.5, "text": "校正的歌词", "words": []},
            {"start": 4, "end": 7, "text": "第二句", "words": []},
        ],
    }
    response = client.post(f"/api/v1/projects/{project['id']}/lyrics", json=document)
    assert response.status_code == 201, response.text
    outputs = response.json()
    assert {a["kind"] for a in outputs} == {"lyrics", "lyrics_lrc", "lyrics_srt"}
    by_kind = {a["kind"]: a for a in outputs}
    assert client.get(by_kind["lyrics"]["contentUrl"]).json()["segments"][0]["text"] == "校正的歌词"
    assert "[00:01.250]校正的歌词" in client.get(by_kind["lyrics_lrc"]["contentUrl"]).text
    assert "00:00:01,250 --> 00:00:03,500" in client.get(by_kind["lyrics_srt"]["contentUrl"]).text
    document["segments"][0]["end"] = 20
    assert client.post(f"/api/v1/projects/{project['id']}/lyrics", json=document).status_code == 422


def test_lrc_import_uses_actual_audio_duration_and_persists(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    response = client.post(
        f"/api/v1/projects/{project['id']}/lyrics/import",
        json={
            "text": "[00:01.00]第一句\n[00:04.00]第二句",
            "durationSeconds": 12,
        },
    )
    assert response.status_code == 201, response.text
    artifact = next(a for a in response.json() if a["kind"] == "lyrics")
    doc = client.get(artifact["contentUrl"]).json()
    assert [s["start"] for s in doc["segments"]] == [1, 4]
    assert doc["segments"][-1]["end"] <= 12


def test_recording_invalid_bytes_do_not_publish_a_result(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    response = client.post(
        f"/api/v1/projects/{project['id']}/recordings?start=1&end=5",
        content=b"not real media",
        headers={"Content-Type": "audio/webm"},
    )
    assert response.status_code == 422, response.text
    assert client.get(f"/api/v1/projects/{project['id']}/artifacts").json() == []


def test_recording_is_finalized_probed_and_available_after_restart(client_factory, tmp_path):
    raw = tmp_path / "capture.webm"
    tools = FFmpegTools.discover()
    subprocess.run(
        [
            str(tools.ffmpeg_path),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "libopus",
            str(raw),
        ],
        check=True,
        capture_output=True,
    )
    with client_factory() as client:
        project, source = project_audio(client, tmp_path)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        response = client.post(
            f"/api/v1/projects/{project['id']}/recordings?start=1&end=5",
            content=raw.read_bytes(),
            headers={"Content-Type": "audio/webm"},
        )
        assert response.status_code == 201, response.text
        artifact = response.json()
        assert artifact["kind"] == "recording"
        assert artifact["metadata"]["selection_start"] == 1
        assert "selection_ranges" not in artifact["metadata"]
        assert 0.9 <= artifact["metadata"]["duration_seconds"] <= 1.2
        assert Path(artifact["path"]).is_relative_to(Path(project["outputDir"]))
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    with client_factory() as restarted:
        outputs = restarted.get(f"/api/v1/projects/{project['id']}/artifacts").json()
        assert any(a["id"] == artifact["id"] for a in outputs)
        response = restarted.get(artifact["contentUrl"], headers={"Range": "bytes=0-31"})
        assert response.status_code == 206 and len(response.content) == 32


def test_recording_rejects_invalid_selection_before_saving(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    for query in ["start=-1&end=3", "start=4&end=2", "start=0&end=99"]:
        response = client.post(
            f"/api/v1/projects/{project['id']}/recordings?{query}",
            content=b"x",
            headers={"Content-Type": "audio/webm"},
        )
        assert response.status_code == 422


def test_recording_directory_failure_releases_the_active_job(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    (Path(project["outputDir"]) / "recordings").write_bytes(b"occupied")
    response = client.post(
        f"/api/v1/projects/{project['id']}/recordings?start=0&end=4",
        content=b"x",
        headers={"Content-Type": "audio/webm"},
    )
    assert response.status_code == 422
    response = client.post(
        f"/api/v1/projects/{project['id']}/process", json={"operation": "prepare"}
    )
    assert response.status_code == 201
    assert terminal(client, response.json()["id"])["status"] == "succeeded"


def test_manual_lyrics_reject_zero_duration_and_nonfinite_duration(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    document = {
        "schemaVersion": 1,
        "durationSeconds": 12,
        "language": "zh",
        "segments": [{"start": 1, "end": 1, "text": "zero range", "words": []}],
    }
    assert client.post(f"/api/v1/projects/{project['id']}/lyrics", json=document).status_code == 422
    document.update(durationSeconds="NaN", segments=[])
    assert client.post(f"/api/v1/projects/{project['id']}/lyrics", json=document).status_code == 422


def test_acknowledged_recording_cancel_reaches_the_finalizer(client, tmp_path, monkeypatch):
    from ai4music_engine.routes import project_outputs

    project, _ = project_audio(client, tmp_path)
    entered, canceled = Event(), Event()

    def slow_finalizer(source, target, token, maximum_duration):
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if token.is_requested:
                canceled.set()
                token.checkpoint()
            time.sleep(0.01)
        raise AssertionError("Capture cancellation did not reach the actual worker")

    monkeypatch.setattr(project_outputs, "finalize_capture", slow_finalizer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post,
            f"/api/v1/projects/{project['id']}/recordings?start=0&end=4",
            content=b"captured bytes",
            headers={"Content-Type": "audio/webm"},
        )
        assert entered.wait(3)
        job = client.get(f"/api/v1/projects/{project['id']}/analyses/latest").json()
        assert client.post(f"/api/v1/analyses/{job['id']}/cancel").status_code == 200
        response = future.result(timeout=5)
    assert canceled.is_set()
    assert response.status_code == 409
    assert client.get(f"/api/v1/analyses/{job['id']}").json()["status"] == "canceled"
    assert client.get(f"/api/v1/projects/{project['id']}/artifacts").json() == []


def test_capture_is_trimmed_to_selection_and_video_flag_comes_from_actual_streams(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    raw = tmp_path / "long-capture.webm"
    tools = FFmpegTools.discover()
    subprocess.run(
        [
            str(tools.ffmpeg_path),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "libopus",
            str(raw),
        ],
        check=True,
        capture_output=True,
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/recordings?start=1&end=2",
        content=raw.read_bytes(),
        headers={"Content-Type": "video/webm"},
    )
    assert response.status_code == 201, response.text
    assert 0.9 <= response.json()["metadata"]["duration_seconds"] <= 1.1
    assert response.json()["metadata"]["has_video"] is False


def test_recording_keeps_real_leading_preparation_silence(client, tmp_path):
    project, _ = project_audio(client, tmp_path)
    raw = tmp_path / "prepared-capture.webm"
    tools = FFmpegTools.discover()
    subprocess.run(
        [
            str(tools.ffmpeg_path),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-c:a",
            "libopus",
            str(raw),
        ],
        check=True,
        capture_output=True,
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/recordings",
        params={"start": 0, "end": 2, "ranges": '[{"start":0,"end":2}]', "lead_in": 2},
        content=raw.read_bytes(),
        headers={"Content-Type": "audio/webm"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["metadata"]["preparation_silence_seconds"] == 2
    assert 3.9 <= response.json()["metadata"]["duration_seconds"] <= 4.1


@pytest.mark.parametrize("lead_in", [-1, 4, "nan", "inf"])
def test_recording_rejects_invalid_preparation_before_creating_job(client, tmp_path, lead_in):
    project, _ = project_audio(client, tmp_path)
    response = client.post(
        f"/api/v1/projects/{project['id']}/recordings",
        params={"start": 0, "end": 2, "lead_in": lead_in},
        content=b"not media",
        headers={"Content-Type": "audio/webm"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "selection_invalid"


@pytest.mark.parametrize(
    "encoded_ranges",
    [
        "not json",
        "",
        "null",
        "{}",
        "[]",
        '[{"start":1}]',
        '[{"start":1,"end":5,"extra":0}]',
        '[{"start":"1","end":5}]',
        '[{"start":true,"end":5}]',
        '[{"start":NaN,"end":5}]',
        '[{"start":1,"end":Infinity}]',
        '[{"start":1,"end":1e10000}]',
        '[{"start":-1,"end":5}]',
        '[{"start":1,"end":1},{"start":2,"end":5}]',
        '[{"start":1,"end":4},{"start":3,"end":5}]',
        '[{"start":4,"end":5},{"start":1,"end":2}]',
        '[{"start":2,"end":5}]',
        '[{"start":1,"end":4}]',
        '[{"start":1,"end":13}]',
        json.dumps([{"start": 1, "end": 5}] * 501),
        " " * 32769 + '[{"start":1,"end":5}]',
        "[" * 1200 + "0" + "]" * 1200,
    ],
    ids=[
        "malformed",
        "empty-query",
        "null",
        "object",
        "empty-list",
        "missing-end",
        "extra-key",
        "string-time",
        "boolean-time",
        "nan",
        "infinity",
        "overflow",
        "negative",
        "empty-range",
        "overlap",
        "unordered",
        "start-mismatch",
        "end-mismatch",
        "beyond-duration",
        "too-many",
        "too-long",
        "too-deep",
    ],
)
def test_invalid_recording_ranges_are_rejected_before_creating_a_job(
    client, tmp_path, encoded_ranges
):
    project, _ = project_audio(client, tmp_path)
    response = client.post(
        f"/api/v1/projects/{project['id']}/recordings",
        params={"start": 1, "end": 5, "ranges": encoded_ranges},
        content=b"not captured media",
        headers={"Content-Type": "audio/webm"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "selection_invalid"
    assert client.get(f"/api/v1/projects/{project['id']}/analyses/latest").status_code == 404
    assert not (Path(project["outputDir"]) / "recordings").exists()


@pytest.mark.parametrize("range_count", [2, 500])
def test_recording_ranges_trim_to_selected_duration_and_survive_restart(
    client_factory, tmp_path, range_count
):
    ranges = (
        [{"start": 1, "end": 1.5}, {"start": 4, "end": 4.75}]
        if range_count == 2
        else [{"start": i * 0.02, "end": i * 0.02 + 0.001} for i in range(500)]
    )
    selected_duration = sum(item["end"] - item["start"] for item in ranges)
    raw = tmp_path / "queued-capture.webm"
    tools = FFmpegTools.discover()
    subprocess.run(
        [
            str(tools.ffmpeg_path),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "libopus",
            str(raw),
        ],
        check=True,
        capture_output=True,
    )
    with client_factory() as client:
        project, source = project_audio(client, tmp_path)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        response = client.post(
            f"/api/v1/projects/{project['id']}/recordings",
            params={
                "start": ranges[0]["start"],
                "end": ranges[-1]["end"],
                "ranges": json.dumps(ranges),
            },
            content=raw.read_bytes(),
            headers={"Content-Type": "audio/webm"},
        )
        assert response.status_code == 201, response.text
        artifact = response.json()
        assert artifact["metadata"]["selection_ranges"] == ranges
        assert artifact["metadata"]["selection_start"] == ranges[0]["start"]
        assert artifact["metadata"]["selection_end"] == ranges[-1]["end"]
        assert artifact["metadata"]["duration_seconds"] == pytest.approx(selected_duration, abs=0.1)
        assert tools.probe(Path(artifact["path"])).duration_seconds == pytest.approx(
            selected_duration, abs=0.1
        )
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    with client_factory() as restarted:
        outputs = restarted.get(f"/api/v1/projects/{project['id']}/artifacts").json()
        assert outputs[0]["metadata"]["selection_ranges"] == ranges
        assert restarted.get(artifact["contentUrl"]).status_code == 200
