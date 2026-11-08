from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import numpy as np
import pytest
import soundfile as sf
from ai4music_core.db.models import AnalysisJob, AnalysisStatus, Artifact
from ai4music_core.media.ffmpeg import FFmpegTools
from ai4music_core.media.hashing import hash_file
from sqlalchemy import select

RATE = 44100


@pytest.fixture
def mix_project(client, tmp_path):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((12 * RATE, 2), dtype=np.float32), RATE, subtype="FLOAT")
    response = client.post(
        "/api/v1/projects", json={"sourcePath": str(source), "outputDir": str(tmp_path / "output")}
    )
    assert response.status_code == 201
    project = response.json()
    with client.app.state.session_factory.begin() as session:
        job = AnalysisJob(project_id=UUID(project["id"]), status=AnalysisStatus.SUCCEEDED)
        session.add(job)
        session.flush()
        for name, base in (("instrumental", 0.125), ("vocals", 0.25)):
            # Distinct per-second amplitudes show which original audio is repeated.
            data = np.repeat(base + np.arange(12) / 64, RATE)
            path = Path(project["outputDir"]) / f"{name}.wav"
            sf.write(path, np.column_stack((data, -data)), RATE, subtype="FLOAT")
            session.add(
                Artifact(
                    project_id=UUID(project["id"]),
                    analysis_id=job.id,
                    kind=name,
                    path=str(path),
                    byte_size=path.stat().st_size,
                    sha256=hash_file(path),
                    content_type="audio/wav",
                )
            )
    return project


def request_mix(client, project, *, prep=3, mode="mixed", vocal=50):
    return client.post(
        f"/api/v1/projects/{project['id']}/recording-mix",
        json={
            "ranges": [{"start": 1, "end": 2}, {"start": 3, "end": 4}],
            "preparationSeconds": prep,
            "preparationAudio": mode,
            "vocalPercent": vocal,
        },
    )


@pytest.mark.parametrize(
    ("prep", "mode", "vocal"),
    [
        (0, "mixed", 0),
        (3, "mixed", 50),
        (10, "vocals", 100),
        (3, "instrumental", 100),
    ],
)
def test_renders_independent_preparations_and_preserves_formal_gains(
    client, mix_project, prep, mode, vocal
):
    response = request_mix(client, mix_project, prep=prep, mode=mode, vocal=vocal)
    assert response.status_code == 201, response.text
    rendered = response.json()
    assert rendered["durationSeconds"] == (prep + 1) * 2
    data = client.get(rendered["contentUrl"], headers={"X-AI4Music-Token": ""})
    assert data.status_code == 200
    samples, rate = sf.read(io.BytesIO(data.content))
    assert rate == RATE
    expected = []
    for start in (1, 3):
        for second in range(start - prep, start):
            backing = 0.125 + second / 64
            voice = 0.25 + second / 64
            expected.append(
                0
                if second < 0
                else {"instrumental": backing, "vocals": voice, "mixed": backing + voice}[mode]
            )
        expected.append(0.125 + start / 64 + (0.25 + start / 64) * vocal / 100)
    assert samples.shape == (len(expected) * RATE, 2)
    assert samples[::RATE, 0] == pytest.approx(expected, abs=1 / 32768)
    assert samples[::RATE, 1] == pytest.approx(-np.array(expected), abs=1 / 32768)
    assert {
        item["kind"]
        for item in client.get(f"/api/v1/projects/{mix_project['id']}/artifacts").json()
    } == {"instrumental", "vocals"}


def test_mix_links_authenticate_ranges_and_expire(client, mix_project, monkeypatch):
    endpoint = f"/api/v1/projects/{mix_project['id']}/recording-mix"
    assert client.post(endpoint, json={}, headers={"X-AI4Music-Token": ""}).status_code == 401
    response = request_mix(client, mix_project)
    assert response.status_code == 201, response.text
    url = response.json()["contentUrl"]
    assert client.get(url + "bad").status_code == 401
    assert client.get(url, headers={"origin": "https://untrusted.example"}).status_code == 403
    partial = client.get(url, headers={"X-AI4Music-Token": "", "Range": "bytes=0-31"})
    assert partial.status_code == 206 and partial.content[:4] == b"RIFF"
    from ai4music_engine.routes.recording_mix import MixCache

    monkeypatch.setattr(MixCache, "now", staticmethod(lambda: 4_000_000_000))
    assert client.get(url).status_code == 401
    client.app.state.recording_mix_cache.prune()
    assert list(client.app.state.recording_mix_cache.directory.iterdir()) == []


def test_mix_refuses_changed_stem_even_when_byte_size_matches(client, mix_project):
    path = Path(mix_project["outputDir"]) / "vocals.wav"
    with path.open("r+b") as handle:
        handle.seek(-1, io.SEEK_END)
        handle.write(b"\x00")
    response = request_mix(client, mix_project)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recording_mix_stems_changed"


def test_mix_reduces_peaks_with_one_gain_for_all_sections(client, mix_project):
    with client.app.state.session_factory.begin() as session:
        for artifact in session.scalars(select(Artifact)):
            level = 0.75 if artifact.kind == "instrumental" else 0.625
            path = Path(artifact.path)
            sf.write(path, np.full((12 * RATE, 2), level), RATE, subtype="FLOAT")
            artifact.sha256 = hash_file(path)
            artifact.byte_size = path.stat().st_size
    rendered = request_mix(client, mix_project, prep=1, mode="vocals", vocal=100)
    assert rendered.status_code == 201, rendered.text
    response = client.get(rendered.json()["contentUrl"])
    samples, _ = sf.read(io.BytesIO(response.content))
    peak = 32767 / 32768
    assert float(np.max(np.abs(samples))) == pytest.approx(peak, abs=1 / 32768)
    assert samples[::RATE, 0] == pytest.approx([0.625 / 1.375 * peak, peak] * 2, abs=1 / 32768)


def test_cache_bounds_renderers_and_evicts_old_idle_links(client, mix_project, monkeypatch):
    monkeypatch.setattr("ai4music_engine.routes.recording_mix._MAX_CACHE_ENTRIES", 1)
    cache = client.app.state.recording_mix_cache
    assert cache.build_slot.acquire(blocking=False)
    try:
        busy = request_mix(client, mix_project)
        assert busy.status_code == 429
        assert busy.json()["error"]["code"] == "recording_mix_busy"
    finally:
        cache.build_slot.release()
    first = request_mix(client, mix_project)
    assert first.status_code == 201
    first_url = urlsplit(first.json()["contentUrl"])
    first_id = UUID(first_url.path.rsplit("/", 1)[1])
    query = parse_qs(first_url.query)
    cache.acquire(first_id, int(query["expires"][0]), query["signature"][0])
    try:
        assert request_mix(client, mix_project, prep=0).status_code == 429
    finally:
        cache.release(first_id)
    second = request_mix(client, mix_project, prep=0)
    assert second.status_code == 201
    assert client.get(first.json()["contentUrl"]).status_code == 404
    assert client.get(second.json()["contentUrl"]).status_code == 200
    assert len(list(cache.directory.iterdir())) == 1


def test_changed_stem_during_render_is_never_published(client, mix_project, monkeypatch):
    from ai4music_engine.routes import recording_mix

    original_render = recording_mix.render_recording_mix

    def change_after_render(backing, vocal, destination, **options):
        duration = original_render(backing, vocal, destination, **options)
        with vocal.open("ab") as handle:
            handle.write(b"changed")
        return duration

    monkeypatch.setattr(recording_mix, "render_recording_mix", change_after_render)
    response = request_mix(client, mix_project)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recording_mix_stems_changed"
    assert list(client.app.state.recording_mix_cache.directory.iterdir()) == []


def test_mix_refuses_a_vocal_stem_from_another_separation(client, mix_project):
    with client.app.state.session_factory.begin() as session:
        project_id = UUID(mix_project["id"])
        job = AnalysisJob(project_id=project_id, status=AnalysisStatus.SUCCEEDED)
        session.add(job)
        session.flush()
        vocal = session.scalar(select(Artifact).where(Artifact.kind == "vocals"))
        assert vocal is not None
        vocal.analysis_id = job.id
    response = request_mix(client, mix_project)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recording_mix_stems_required"


@pytest.mark.parametrize(
    ("prep", "mode", "vocal"),
    [
        (11, "mixed", 0),
        (0, "source", 0),
        (0, "mixed", 101),
        (True, "mixed", 50),
        (3, "vocals", 0.5),
    ],
)
def test_mix_rejects_invalid_controls(client, mix_project, prep, mode, vocal):
    assert request_mix(client, mix_project, prep=prep, mode=mode, vocal=vocal).status_code == 422


@pytest.mark.parametrize(("input_seconds", "saved_seconds"), [(8.7, 8.7), (12, 10)])
def test_recording_retains_mix_and_time_for_each_original_range(
    client, mix_project, tmp_path, input_seconds, saved_seconds
):
    take = tmp_path / "take.webm"
    tools = FFmpegTools.discover()
    subprocess.run(
        [
            str(tools.ffmpeg_path),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1000:duration={input_seconds}",
            "-c:a",
            "libopus",
            str(take),
        ],
        check=True,
        capture_output=True,
    )
    ranges = [{"start": 1, "end": 2}, {"start": 3, "end": 4}]
    mix = {
        "preparationSeconds": 3,
        "preparationAudio": "vocals",
        "vocalPercent": 50,
        "includeBacking": False,
    }
    url = f"/api/v1/projects/{mix_project['id']}/recordings"
    params = {"start": 1, "end": 4, "ranges": json.dumps(ranges), "mix": json.dumps(mix)}
    headers = {"Content-Type": "audio/webm"}
    rejected = client.post(
        url, params={**params, "lead_in": 1}, content=take.read_bytes(), headers=headers
    )
    assert rejected.status_code == 422
    response = client.post(url, params=params, content=take.read_bytes(), headers=headers)
    assert response.status_code == 201, response.text
    metadata = response.json()["metadata"]
    assert metadata["mix"] == mix
    assert metadata["selection_ranges"] == ranges
    assert metadata["duration_seconds"] == pytest.approx(saved_seconds, abs=0.06)
