from __future__ import annotations

import hashlib
import json
import shutil
import struct
import time
import wave
from pathlib import Path

import pytest
from ai4music_core.lyrics.bailian import BailianProvider
from ai4music_core.lyrics.provider import LyricsResult
from ai4music_core.separation import DemucsProvider
from ai4music_core.separation.provider import SeparationResult
from fastapi.testclient import TestClient


def create_audio_project(
    client: TestClient, tmp_path: Path, *, valid: bool = True
) -> tuple[dict, Path]:
    source = tmp_path / "真实 音频.wav"
    if valid:
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(struct.pack("<h", 1000) * 16000)
    else:
        source.write_bytes(b"not audio despite wav extension")
    response = client.post(
        "/api/v1/projects", json={"sourcePath": str(source), "outputDir": str(tmp_path / "output")}
    )
    assert response.status_code == 201
    return response.json(), source


def wait_terminal(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/analyses/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"succeeded", "failed", "canceled"}:
            return job
        time.sleep(0.03)
    raise AssertionError("Audio processing did not finish")


def test_preparation_runs_real_decode_and_publishes_playable_derived_audio(
    client: TestClient, tmp_path: Path
):
    project, source = create_audio_project(client, tmp_path)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    created = client.post(
        f"/api/v1/projects/{project['id']}/process", json={"operation": "prepare"}
    )
    assert created.status_code == 201
    job = wait_terminal(client, created.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["operation"] == "prepare"
    assert [stage["name"] for stage in job["stages"]] == [
        "fingerprint_source",
        "validate_media",
        "normalize_audio",
    ]
    assert all(stage["status"] == "succeeded" for stage in job["stages"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    outputs = client.get(f"/api/v1/projects/{project['id']}/artifacts").json()
    assert len(outputs) == 1
    artifact = outputs[0]
    assert artifact["kind"] == "normalized_audio"
    assert artifact["metadata"]["sample_rate"] == 44100
    assert artifact["metadata"]["channels"] == 2
    assert artifact["metadata"]["codec"] == "pcm_f32le"
    assert Path(artifact["path"]).resolve() != source.resolve()
    content = client.get(artifact["contentUrl"], headers={"Range": "bytes=0-63"})
    assert content.status_code == 206
    assert len(content.content) == 64
    assert content.content[:4] == b"RIFF"
    artifact_path = Path(artifact["path"])
    artifact_path.rename(artifact_path.with_suffix(".moved"))
    assert client.get(f"/api/v1/projects/{project['id']}/artifacts").json() == []
    assert client.get(artifact["contentUrl"]).status_code == 404


def test_audio_validation_rejects_bytes_that_merely_have_an_audio_extension(
    client: TestClient, tmp_path: Path
):
    project, _ = create_audio_project(client, tmp_path, valid=False)
    created = client.post(
        f"/api/v1/projects/{project['id']}/process", json={"operation": "prepare"}
    )
    assert created.status_code == 201
    job = wait_terminal(client, created.json()["id"])
    assert job["status"] == "failed"
    assert job["errorCode"] == "media_invalid"
    assert job["stage"]["name"] == "validate_media"
    assert client.get(f"/api/v1/projects/{project['id']}/artifacts").json() == []


def test_playback_and_download_grants_are_scoped_and_support_byte_ranges(client, tmp_path):
    project, _ = create_audio_project(client, tmp_path)
    created = client.post(
        f"/api/v1/projects/{project['id']}/process", json={"operation": "prepare"}
    )
    assert wait_terminal(client, created.json()["id"])["status"] == "succeeded"
    artifact = client.get(f"/api/v1/projects/{project['id']}/artifacts").json()[0]
    endpoint = artifact["contentUrl"] + "/playback"
    anonymous = {"X-AI4Music-Token": "", "Range": "bytes=0-63"}
    assert client.get(endpoint, headers=anonymous).status_code == 401
    assert client.get(artifact["contentUrl"], headers=anonymous).status_code == 401
    grant = client.get(endpoint).json()["contentUrl"]
    response = client.get(grant, headers=anonymous)
    assert response.status_code == 206
    assert response.content[:4] == b"RIFF"
    assert response.headers["content-disposition"].startswith("inline")
    assert client.get(grant + "x", headers=anonymous).status_code == 401
    assert (
        client.get(grant.replace(artifact["id"], project["id"]), headers=anonymous).status_code
        == 401
    )
    download = client.get(endpoint, params={"download": True}).json()["contentUrl"]
    response = client.get(download, headers=anonymous)
    assert response.status_code == 206
    assert response.headers["content-disposition"].startswith("attachment")
    Path(artifact["path"]).unlink()
    assert client.get(grant, headers=anonymous).status_code == 404


@pytest.mark.parametrize("vocal_state", ["ready", "missing", "changed"])
def test_singing_is_one_durable_pipeline_using_its_own_separated_vocals(
    client, tmp_path, monkeypatch, vocal_state
):
    # The model boundaries are replaced; decoding, stage scheduling, artifacts,
    # and persistence all use the real engine.
    prepared = {
        "path": str(tmp_path / "test-model"),
        "model_id": "test-model",
        "revision": "test-revision",
        "model_sha256": "a" * 64,
    }
    separated_paths = []
    transcribed_paths = []

    def prepare(_self, *_args, **_kwargs):
        return prepared

    def separate(_self, source, target, **_kwargs):
        target.mkdir(parents=True)
        vocals, instrumental = target / "vocals.wav", target / "instrumental.wav"
        shutil.copyfile(source, vocals)
        shutil.copyfile(source, instrumental)
        separated_paths.append(vocals)
        return SeparationResult(
            vocals, instrumental, "cpu", "test-model", "test-revision", "a" * 64
        )

    def prepare_lyrics(_self, *_args, **_kwargs):
        current = separated_paths[-1]
        if vocal_state == "missing":
            current.unlink()
        elif vocal_state == "changed":
            data = current.read_bytes()
            current.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        return prepared

    def transcribe(_self, source, target, **_kwargs):
        transcribed_paths.append(source)
        target.mkdir(parents=True)
        json_path, lrc_path, srt_path = (
            target / "lyrics.json",
            target / "lyrics.lrc",
            target / "lyrics.srt",
        )
        json_path.write_text(json.dumps({"segments": []}))
        lrc_path.write_text("")
        srt_path.write_text("")
        return LyricsResult(
            json_path, lrc_path, srt_path, "cpu", "test-model", "test-revision", "a" * 64
        )

    monkeypatch.setattr(DemucsProvider, "prepare", prepare)
    monkeypatch.setattr(DemucsProvider, "separate", separate)
    monkeypatch.setattr(BailianProvider, "prepare", prepare_lyrics)
    monkeypatch.setattr(BailianProvider, "transcribe", transcribe)
    project, original = create_audio_project(client, tmp_path)
    # An older published vocal must never win over this pipeline's vocal.
    previous = client.post(
        f"/api/v1/projects/{project['id']}/process", json={"operation": "separate"}
    )
    assert previous.status_code == 201
    assert wait_terminal(client, previous.json()["id"])["status"] == "succeeded"
    assert list(Path(project["outputDir"]).rglob("normalized.wav")) == []
    artifacts = client.get(f"/api/v1/projects/{project['id']}/artifacts").json()
    source = next(item for item in artifacts if item["kind"] == "normalized_audio")
    assert Path(source["path"]) == original
    assert source["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert (
        client.get(source["contentUrl"], headers={"Range": "bytes=0-63"}).content
        == original.read_bytes()[:64]
    )
    route = f"/api/v1/projects/{project['id']}/process"
    assert client.post(route, json={"operation": "singing"}).status_code == 422
    created = client.post(route, json={"operation": "singing", "allowCloudUpload": True})
    assert created.status_code == 201, created.text
    job = wait_terminal(client, created.json()["id"])
    assert [stage["name"] for stage in job["stages"]] == [
        "fingerprint_source",
        "validate_media",
        "prepare_model",
        "separate_stems",
        "prepare_lyrics_model",
        "transcribe_lyrics",
    ]
    assert len(separated_paths) == 2
    if vocal_state == "ready":
        assert job["status"] == "succeeded", job
        assert all(stage["status"] == "succeeded" for stage in job["stages"])
        assert transcribed_paths == [separated_paths[-1]]
        outputs = client.get(f"/api/v1/projects/{project['id']}/artifacts").json()
        lyrics = next(item for item in outputs if item["kind"] == "lyrics")
        assert lyrics["analysisId"] == job["id"]
        assert lyrics["metadata"]["input_kind"] == "vocals"
    else:
        assert job["status"] == "failed", job
        assert job["errorCode"] == (
            "vocal_output_missing" if vocal_state == "missing" else "output_changed"
        )
        assert transcribed_paths == []
    replay = client.get(f"/api/v1/analyses/{job['id']}/events")
    starts = [
        json.loads(line.removeprefix("data: "))["payload"]["stageName"]
        for line in replay.text.splitlines()
        if line.startswith("data: ")
        and json.loads(line.removeprefix("data: "))["type"]
        in {"FINGERPRINT_STARTED", "STAGE_STARTED"}
    ]
    assert starts == [stage["name"] for stage in job["stages"]]
