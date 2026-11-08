from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from threading import Event

ProgressCallback = Callable[[int, int], None]


class JobCanceled(Exception):
    """Raised at a safe streaming checkpoint after cancellation is requested."""


class CancellationToken:
    def __init__(self) -> None:
        self._requested = Event()

    @property
    def is_requested(self) -> bool:
        return self._requested.is_set()

    def request(self) -> None:
        self._requested.set()

    def checkpoint(self) -> None:
        if self.is_requested:
            raise JobCanceled


def hash_file(
    path: Path,
    *,
    chunk_size: int = 4 * 1024 * 1024,
    cancellation: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    token = cancellation or CancellationToken()
    total = path.stat().st_size
    completed = 0
    digest = hashlib.sha256()

    with path.open("rb") as source:
        while True:
            token.checkpoint()
            chunk = source.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            completed += len(chunk)
            if on_progress is not None:
                on_progress(completed, total)

    token.checkpoint()
    if total == 0 and on_progress is not None:
        on_progress(0, 0)
    return digest.hexdigest()
