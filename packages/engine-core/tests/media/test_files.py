from __future__ import annotations

from pathlib import Path

import pytest
from ai4music_core.media.files import (
    OutputDirectoryError,
    SourceFileError,
    inspect_source,
    prepare_project_directory,
)


def test_inspect_source_returns_metadata_without_copying(tmp_path: Path) -> None:
    source = tmp_path / "示例 音频.wav"
    payload = b"RIFF-local-source"
    source.write_bytes(payload)

    snapshot = inspect_source(str(source))

    assert snapshot.path == source.resolve()
    assert snapshot.size == len(payload)
    assert snapshot.mtime_ns == source.stat().st_mtime_ns
    assert source.read_bytes() == payload


def test_inspect_source_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SourceFileError, match="source_missing") as error:
        inspect_source(str(tmp_path / "missing.wav"))

    assert error.value.code == "source_missing"


def test_inspect_source_rejects_a_directory(tmp_path: Path) -> None:
    with pytest.raises(SourceFileError) as error:
        inspect_source(str(tmp_path))

    assert error.value.code == "source_not_file"


@pytest.mark.parametrize(
    "raw_path",
    [
        "https://example.com/audio.wav",
        "bad\x00path.wav",
        r"\\.\pipe\ai4music",
        r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\audio.wav",
    ],
)
def test_inspect_source_rejects_non_file_inputs(raw_path: str) -> None:
    with pytest.raises(SourceFileError) as error:
        inspect_source(raw_path)

    assert error.value.code == "source_invalid"


def test_prepare_project_directory_is_non_conflicting(tmp_path: Path) -> None:
    output_root = tmp_path / "exports"

    first = prepare_project_directory(str(output_root), "Demo Track")
    second = prepare_project_directory(str(output_root), "Demo Track")

    assert first == output_root / "Demo Track"
    assert second == output_root / "Demo Track-2"
    assert first.is_dir()
    assert second.is_dir()


def test_prepare_project_directory_requires_an_existing_parent(tmp_path: Path) -> None:
    output_root = tmp_path / "missing-parent" / "exports"

    with pytest.raises(OutputDirectoryError) as error:
        prepare_project_directory(str(output_root), "Demo")

    assert error.value.code == "output_parent_missing"
