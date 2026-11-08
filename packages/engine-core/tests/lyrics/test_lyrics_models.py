from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ai4music_core.media.hashing import CancellationToken, JobCanceled


def spec_for(tmp_path: Path):
    from ai4music_core.lyrics.models import ModelFile, ModelSpec

    source = tmp_path / "origin"
    source.mkdir()
    files = []
    for name, data in (("model.bin", b"weights"), ("config.json", b"{}")):
        (source / name).write_bytes(data)
        files.append(ModelFile(name, hashlib.sha256(data).hexdigest(), len(data)))
    return ModelSpec("test-model", "abc123", source.as_uri(), tuple(files))


def test_model_download_is_atomic_and_every_cached_file_is_verified(tmp_path: Path) -> None:
    from ai4music_core.lyrics.models import ensure_model

    spec = spec_for(tmp_path)
    cache = tmp_path / "cache"
    events = []
    path = ensure_model(cache, spec, CancellationToken(), on_event=events.append)
    assert (path / "model.bin").read_bytes() == b"weights"
    assert not list(cache.rglob("*.part"))
    assert {event["phase"] for event in events} == {"model_download", "model_verify"}
    # A receipt must never make a corrupt cache trusted.
    (path / "config.json").write_bytes(b"BAD")
    with pytest.raises(ValueError, match="model_checksum_mismatch"):
        ensure_model(cache, spec, CancellationToken())


def test_bad_download_never_publishes_the_model_directory(tmp_path: Path) -> None:
    from ai4music_core.lyrics.models import ensure_model

    spec = spec_for(tmp_path)
    (tmp_path / "origin" / "model.bin").write_bytes(b"tampered")
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="model_checksum_mismatch"):
        ensure_model(cache, spec, CancellationToken())
    assert not (cache / spec.model_id / spec.revision).exists()
    assert not list(cache.rglob("*.part"))


def test_canceled_download_cleans_up_and_can_be_retried(tmp_path: Path) -> None:
    from ai4music_core.lyrics.models import ensure_model

    spec = spec_for(tmp_path)
    cache = tmp_path / "cache"
    token = CancellationToken()
    with pytest.raises(JobCanceled):
        ensure_model(cache, spec, token, on_event=lambda _event: token.request())
    assert not (cache / spec.model_id / spec.revision).exists()
    assert not list(cache.rglob("*.part"))
    assert ensure_model(cache, spec, CancellationToken()).is_dir()


def test_fixed_model_is_multilingual_and_pinned_to_complete_file_hashes() -> None:
    from ai4music_core.lyrics.models import SMALL_MODEL as DEFAULT_MODEL

    assert DEFAULT_MODEL.model_id == "faster-whisper-small"
    assert DEFAULT_MODEL.revision == "536b0662742c02347bc0e980a01041f333bce120"
    assert sum(f.size_bytes for f in DEFAULT_MODEL.files) == 486212372
    assert len(DEFAULT_MODEL.revision) == 40
    assert {f.filename for f in DEFAULT_MODEL.files} == {
        "model.bin",
        "config.json",
        "tokenizer.json",
        "vocabulary.txt",
    }
    assert all(len(f.sha256) == 64 and f.size_bytes > 0 for f in DEFAULT_MODEL.files)


def test_small_manifest_matches_the_actual_runtime_pin() -> None:
    import json
    from dataclasses import asdict

    from ai4music_core.lyrics.models import SMALL_MODEL as DEFAULT_MODEL

    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / "model-registry/whisper-small.json").read_text(encoding="utf-8"))
    assert manifest["files"] == [asdict(item) for item in DEFAULT_MODEL.files]
    assert manifest["model_sha256"] == DEFAULT_MODEL.sha256
    assert manifest["size_bytes"] == sum(f.size_bytes for f in DEFAULT_MODEL.files)


def test_default_quality_model_and_alignment_download_are_pinned() -> None:
    from ai4music_core.lyrics.models import DEFAULT_MODEL, SMALL_MODEL

    assert DEFAULT_MODEL.model_id == "qwen3-asr-0.6b-hf"
    assert DEFAULT_MODEL.revision == "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c"
    assert sum(f.size_bytes for f in DEFAULT_MODEL.files) == 1576363223
    assert sum(f.size_bytes for m in (DEFAULT_MODEL, SMALL_MODEL) for f in m.files) == 2062575595
    import json
    from dataclasses import asdict

    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / "model-registry/qwen3-asr-0.6b.json").read_text(encoding="utf-8"))
    assert manifest["files"] == [asdict(item) for item in DEFAULT_MODEL.files]
    assert manifest["model_sha256"] == DEFAULT_MODEL.sha256


def test_short_http_response_resumes_only_from_verified_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from ai4music_core.lyrics.models import ModelFile, ModelSpec, ensure_model

    data = b"complete model bytes"
    spec = ModelSpec(
        "http-test",
        "pinned",
        "https://official.invalid/pinned",
        (ModelFile("model.bin", hashlib.sha256(data).hexdigest(), len(data)),),
    )
    requests = []

    class Response(io.BytesIO):
        def __init__(self, content, *, partial):
            super().__init__(content)
            self.status = 206 if partial else 200
            self.headers = (
                {"Content-Range": f"bytes 5-{len(data) - 1}/{len(data)}"} if partial else {}
            )

    def open_response(request, **_kwargs):
        requests.append(request)
        if len(requests) == 1:
            return Response(data[:5], partial=False)
        assert request.get_header("Range") == "bytes=5-"
        return Response(data[5:], partial=True)

    monkeypatch.setattr("ai4music_core.lyrics.models.urlopen", open_response)
    path = ensure_model(tmp_path, spec, CancellationToken())
    assert (path / "model.bin").read_bytes() == data
    assert len(requests) == 2
