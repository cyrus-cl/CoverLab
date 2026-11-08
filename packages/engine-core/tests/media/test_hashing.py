from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ai4music_core.media.hashing import CancellationToken, JobCanceled, hash_file


def test_hash_file_reports_real_byte_progress(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * 10)
    progress: list[tuple[int, int]] = []

    digest = hash_file(
        source,
        chunk_size=4,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert digest == hashlib.sha256(b"a" * 10).hexdigest()
    assert progress == [(4, 10), (8, 10), (10, 10)]


def test_hash_file_stops_at_a_cancellation_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * 10)
    cancel = CancellationToken()

    with pytest.raises(JobCanceled):
        hash_file(
            source,
            chunk_size=4,
            cancellation=cancel,
            on_progress=lambda done, _total: cancel.request() if done == 4 else None,
        )
