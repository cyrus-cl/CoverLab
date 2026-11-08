"""Prepare only the hash-pinned LGPL shared FFmpeg runtime used by the installer.

No PATH discovery: the distribution always comes from the reviewed manifest.
Downloaded archives and extracted files live under the ignored .runtime tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "licenses" / "ffmpeg" / "runtime-manifest.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_path(directory: Path, name: str) -> Path:
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe runtime member: {name}")
    path = directory.joinpath(*parts.parts)
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Unsafe runtime member: {name}")
    return path


def verify_runtime(directory: Path, manifest: dict[str, Any]) -> None:
    for name, digest in manifest["files"].items():
        path = checked_path(directory, name)
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"FFmpeg runtime file hash mismatch: {name}")
    expected = {name for name in manifest["files"] if name.startswith("bin/")}
    actual = {p.relative_to(directory).as_posix() for p in (directory / "bin").glob("*")}
    if actual != expected:
        raise ValueError("FFmpeg runtime contains unreviewed binaries")


def extract_runtime(archive: Path, destination: Path, manifest: dict[str, Any]) -> None:
    if sha256(archive) != manifest["sha256"]:
        raise ValueError("FFmpeg archive hash mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ffmpeg-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "runtime"
        with ZipFile(archive) as zipped:
            for name in manifest["files"]:
                target = checked_path(staged, name)
                member = manifest["archive_root"] + "/" + name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        verify_runtime(staged, manifest)
        staged.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--archive", type=Path, help="Use a previously downloaded pinned ZIP")
    arguments = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    destination = checked_path(ROOT / ".runtime" / "ffmpeg", manifest["runtime_id"])
    if not destination.exists() and not arguments.verify_only:
        archive = arguments.archive
        if archive is None:
            downloads = ROOT / ".runtime" / "media-downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            archive = downloads / (manifest["archive_root"] + ".zip")
            if not archive.exists():
                partial = archive.with_suffix(".zip.partial")
                try:
                    with urlopen(manifest["url"], timeout=60) as source, partial.open("wb") as out:
                        shutil.copyfileobj(source, out)
                    if sha256(partial) != manifest["sha256"]:
                        raise ValueError("FFmpeg downloaded archive hash mismatch")
                    partial.replace(archive)
                finally:
                    partial.unlink(missing_ok=True)
        extract_runtime(archive, destination, manifest)
    verify_runtime(destination, manifest)
    if not arguments.verify_only:
        licenses = destination / "licenses"
        licenses.mkdir(exist_ok=True)
        for source in MANIFEST.parent.iterdir():
            if source.is_file():
                shutil.copyfile(source, licenses / source.name)
        for option, filename in (
            ("-version", "VERSION.txt"),
            ("-buildconf", "BUILD_CONFIGURATION.txt"),
            ("-L", "RUNTIME_LICENSE.txt"),
        ):
            result = subprocess.run(
                [str(destination / "bin" / "ffmpeg.exe"), option],
                check=True,
                capture_output=True,
                timeout=30,
            )
            (licenses / filename).write_bytes(result.stdout + result.stderr)
    print(destination)


if __name__ == "__main__":
    main()
