from __future__ import annotations

import hashlib
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from ai4music_core.media.hashing import CancellationToken, JobCanceled


def test_official_weights_are_hashed_before_cache_publication(tmp_path: Path) -> None:
    from ai4music_core.separation.models import ModelSpec, ensure_model

    payload = b"known model bytes" * 1000
    digest = hashlib.sha256(payload).hexdigest()
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "model.th").write_bytes(payload)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(remote))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        spec = ModelSpec(
            "test",
            "revision",
            "model.th",
            f"http://127.0.0.1:{server.server_port}/model.th",
            digest,
            digest[:8],
        )
        progress: list[tuple[int, int]] = []
        result = ensure_model(
            tmp_path / "cache",
            spec,
            CancellationToken(),
            on_progress=lambda a, b: progress.append((a, b)),
        )
        assert result.read_bytes() == payload
        assert progress[-1] == (len(payload), len(payload))
        receipt = json.loads((result.parent / "manifest.json").read_text())
        assert receipt["sha256"] == digest

        # A cached checkpoint must be rechecked, not trusted by filename or receipt.
        result.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="checksum"):
            ensure_model(tmp_path / "cache", spec, CancellationToken())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_download_with_wrong_full_hash_is_never_published(tmp_path: Path) -> None:
    from ai4music_core.separation.models import ModelSpec, ensure_model

    source = tmp_path / "wrong.th"
    source.write_bytes(b"incorrect model")
    spec = ModelSpec("test", "rev", "model.th", source.as_uri(), "0" * 64, "00000000")
    with pytest.raises(ValueError, match="checksum"):
        ensure_model(tmp_path / "cache", spec, CancellationToken())
    assert not (tmp_path / "cache" / "model.th").exists()
    assert not list((tmp_path / "cache").glob("*.part"))


def test_pre_canceled_model_preparation_creates_nothing(tmp_path: Path) -> None:
    from ai4music_core.separation.models import DEFAULT_MODEL, ensure_model

    token = CancellationToken()
    token.request()
    with pytest.raises(JobCanceled):
        ensure_model(tmp_path / "cache", DEFAULT_MODEL, token)
    assert not (tmp_path / "cache").exists()


def test_download_cancellation_removes_partial_checkpoint(tmp_path: Path) -> None:
    from ai4music_core.separation.models import ModelSpec, ensure_model

    payload = b"a" * (3 * 1024 * 1024)
    source = tmp_path / "source.th"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    spec = ModelSpec("test", "rev", "model.th", source.as_uri(), digest, digest[:8])
    token = CancellationToken()
    with pytest.raises(JobCanceled):
        ensure_model(
            tmp_path / "cache",
            spec,
            token,
            on_progress=lambda completed, _total: token.request() if completed else None,
        )
    assert not list((tmp_path / "cache").iterdir())
