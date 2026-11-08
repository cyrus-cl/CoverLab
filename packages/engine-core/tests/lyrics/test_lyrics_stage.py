from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from ai4music_core.media.hashing import CancellationToken, hash_file


def test_transcription_stage_registers_immutable_raw_recognition_for_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai4music_core.jobs.analysis_processing import execute_analysis_stage
    from ai4music_core.lyrics import LyricsResult

    source = tmp_path / "vocals.wav"
    source.write_bytes(b"existing audio")
    repository = SimpleNamespace(
        current_job_artifact=lambda *_: {
            "path": str(source),
            "sha256": hash_file(source),
            "metadata": {"duration_seconds": 2},
        },
        latest_artifact=lambda *_: None,
    )
    claimed = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        output_dir=tmp_path,
        operation="lyrics",
        allow_cloud_upload=True,
    )

    class Provider:
        def transcribe(self, path, target, **_kwargs):
            assert path == source
            assert _kwargs["duration"] == 2
            target.mkdir(parents=True)
            paths = [
                target / name
                for name in ("lyrics.json", "lyrics.lrc", "lyrics.srt", "lyrics.raw.json")
            ]
            for item in paths:
                item.write_text("model bytes retained", encoding="utf-8")
            return LyricsResult(
                *paths[:3], "cpu", "faster-whisper-small", "revision", "digest", paths[3]
            )

    monkeypatch.setattr("ai4music_core.jobs.analysis_processing.BailianProvider", Provider)
    metadata, artifacts = execute_analysis_stage(
        repository,
        claimed,
        "transcribe_lyrics",
        CancellationToken(),
        lambda *_: None,
    )
    raw = next(a for a in artifacts if a["kind"] == "lyrics_raw")
    assert Path(raw["path"]).read_text(encoding="utf-8") == "model bytes retained"
    assert len(raw["sha256"]) == 64
    assert metadata["machine_draft"] is True
    assert source.read_bytes() == b"existing audio"
