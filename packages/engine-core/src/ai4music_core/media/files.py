from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

URL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
INVALID_WINDOWS_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WINDOWS_NAMES = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    path: Path
    size: int
    mtime_ns: int


class SourceFileError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OutputDirectoryError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _is_unsafe_path(raw_path: str) -> bool:
    if not raw_path or "\x00" in raw_path or URL_PATTERN.match(raw_path):
        return True
    normalized = raw_path.replace("/", "\\").casefold()
    if normalized.startswith(("\\\\.\\", "\\\\?\\globalroot\\")):
        return True
    filename = Path(raw_path).name
    reserved_name = filename.split(".", 1)[0].upper()
    return reserved_name in RESERVED_WINDOWS_NAMES


def inspect_source(raw_path: str) -> SourceSnapshot:
    if _is_unsafe_path(raw_path):
        raise SourceFileError("source_invalid")
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise SourceFileError("source_missing") from error
    except (OSError, RuntimeError, ValueError) as error:
        raise SourceFileError("source_invalid") from error
    if not path.is_file():
        raise SourceFileError("source_not_file")
    stat = path.stat()
    return SourceSnapshot(path=path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _safe_directory_name(name: str) -> str:
    sanitized = INVALID_WINDOWS_CHARACTERS.sub("-", name).strip(" .")
    sanitized = re.sub(r"\s+", " ", sanitized)
    if not sanitized or sanitized.split(".", 1)[0].upper() in RESERVED_WINDOWS_NAMES:
        return "AI4Music Project"
    return sanitized[:120].rstrip(" .")


def _prepare_output_root(raw_output_dir: str) -> Path:
    if _is_unsafe_path(raw_output_dir):
        raise OutputDirectoryError("output_invalid")
    requested = Path(raw_output_dir).expanduser()
    try:
        if requested.exists():
            output_root = requested.resolve(strict=True)
            if not output_root.is_dir():
                raise OutputDirectoryError("output_not_directory")
            return output_root
        parent = requested.parent.resolve(strict=True)
    except FileNotFoundError as error:
        raise OutputDirectoryError("output_parent_missing") from error
    except (OSError, RuntimeError, ValueError) as error:
        raise OutputDirectoryError("output_invalid") from error
    if not parent.is_dir():
        raise OutputDirectoryError("output_parent_missing")
    output_root = parent / requested.name
    try:
        output_root.mkdir()
    except FileExistsError:
        if not output_root.is_dir():
            raise OutputDirectoryError("output_not_directory") from None
    except OSError as error:
        raise OutputDirectoryError("output_unavailable") from error
    return output_root.resolve(strict=True)


def prepare_project_directory(raw_output_dir: str, project_name: str) -> Path:
    output_root = _prepare_output_root(raw_output_dir)
    base_name = _safe_directory_name(project_name)
    suffix = 1
    while True:
        directory_name = base_name if suffix == 1 else f"{base_name}-{suffix}"
        candidate = output_root / directory_name
        try:
            candidate.mkdir()
        except FileExistsError:
            suffix += 1
            continue
        except OSError as error:
            raise OutputDirectoryError("output_unavailable") from error
        return candidate.resolve(strict=True)
