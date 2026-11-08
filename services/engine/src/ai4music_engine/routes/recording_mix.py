"""Render temporary recording guides and expose scoped loopback media links."""

from __future__ import annotations

import asyncio
import hmac
import math
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock
from uuid import UUID, uuid4

from ai4music_core.db.models import AnalysisJob, AnalysisStatus, Artifact
from ai4music_core.media.ffmpeg import MediaToolError
from ai4music_core.media.hashing import hash_file
from ai4music_core.media.recording_mix import (
    MAX_MIX_SECONDS,
    MIX_FRAME_BYTES,
    MIX_RATE,
    render_recording_mix,
)
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from ai4music_engine.errors import LocalEngineError
from ai4music_engine.recording_selection import (
    MAX_SELECTION_QUERY_BYTES,
    MixRequest,
    validate_recording_ranges,
)
from ai4music_engine.routes.audio import artifact_path, is_current_artifact
from ai4music_engine.routes.project_outputs import project_context

mix_router = APIRouter(tags=["recording mix"])
mix_media_router = APIRouter(prefix="/api/v1", tags=["recording mix"])
_MIX_TTL_SECONDS = 30 * 60
_MAX_CACHE_BYTES = 512 * 1024 * 1024
_MAX_CACHE_ENTRIES = 12


@dataclass
class _MixEntry:
    expires: int
    byte_size: int
    readers: int = 0


def _file_snapshot(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@dataclass(frozen=True)
class _VerifiedStem:
    path: Path
    snapshot: tuple[int, ...]

    def verify_unchanged(self) -> None:
        try:
            unchanged = _file_snapshot(self.path) == self.snapshot
        except OSError:
            unchanged = False
        if not unchanged:
            raise LocalEngineError(
                status_code=409,
                code="recording_mix_stems_changed",
                message="分离音轨已变更，请重新生成。",
            )


class MixCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.build_slot = BoundedSemaphore()
        self._lock = Lock()
        self._key = secrets.token_bytes(32)
        self._entries: dict[UUID, _MixEntry] = {}

    @staticmethod
    def now() -> int:
        return int(time.time())

    def path(self, mix_id: UUID) -> Path:
        return self.directory / f"{mix_id}.wav"

    def _remove(self, mix_id: UUID) -> None:
        try:
            self.path(mix_id).unlink(missing_ok=True)
        except OSError:
            return
        self._entries.pop(mix_id)

    def prune(self) -> None:
        now = self.now()
        with self._lock:
            for mix_id, entry in list(self._entries.items()):
                if not entry.readers and entry.expires <= now:
                    self._remove(mix_id)

    def reserve(self, byte_size: int) -> None:
        self.prune()
        with self._lock:
            for mix_id in list(self._entries):
                if self._has_capacity(byte_size):
                    break
                if not self._entries[mix_id].readers:
                    self._remove(mix_id)
            if not self._has_capacity(byte_size):
                raise LocalEngineError(
                    status_code=429,
                    code="recording_mix_busy",
                    message="本机混音缓存正在使用，请稍后重试。",
                )

    def _has_capacity(self, byte_size: int) -> bool:
        return (
            len(self._entries) < _MAX_CACHE_ENTRIES
            and byte_size + sum(entry.byte_size for entry in self._entries.values())
            <= _MAX_CACHE_BYTES
        )

    def _signature(self, mix_id: UUID, expires: int) -> bytes:
        scope = f"coverlab-recording-mix-v1:{mix_id}:{expires}"
        return hmac.digest(self._key, scope.encode(), "sha256").hex().encode()

    def publish(self, mix_id: UUID, byte_size: int) -> str:
        expires = self.now() + _MIX_TTL_SECONDS
        with self._lock:
            self._entries[mix_id] = _MixEntry(expires, byte_size)
        return f"/api/v1/mixes/{mix_id}?expires={expires}&signature={self._signature(mix_id, expires).decode()}"

    def acquire(self, mix_id: UUID, expires: int, signature: str) -> Path:
        if expires <= self.now() or not hmac.compare_digest(
            signature.encode(), self._signature(mix_id, expires)
        ):
            raise LocalEngineError(
                status_code=401,
                code="invalid_recording_mix_link",
                message="录制混音链接无效或已过期。",
            )
        with self._lock:
            entry = self._entries.get(mix_id)
            path = self.path(mix_id)
            if entry is None or entry.expires != expires or not path.is_file():
                raise LocalEngineError(
                    status_code=404,
                    code="recording_mix_missing",
                    message="本机混音已失效，请重新生成。",
                )
            entry.readers += 1
            return path

    def release(self, mix_id: UUID) -> None:
        with self._lock:
            entry = self._entries[mix_id]
            entry.readers -= 1
            if not entry.readers and entry.expires <= self.now():
                self._remove(mix_id)


def _remove_abandoned_mixes(directory: Path) -> None:
    # A killed engine may leave its private temp directory behind. Only remove
    # stale files with the names this cache creates inside that namespace.
    cutoff = time.time() - 24 * 60 * 60
    for child in directory.glob("recording-mixes-*"):
        try:
            if (
                child.is_symlink()
                or child.is_junction()
                or not child.is_dir()
                or child.stat().st_mtime >= cutoff
            ):
                continue
            for wav in child.glob("*.wav"):
                try:
                    UUID(wav.stem)
                except ValueError:
                    continue
                if wav.lstat().st_mtime < cutoff:
                    wav.unlink()
            child.rmdir()
        except OSError:
            continue


@asynccontextmanager
async def recording_mix_cache(cache_dir: Path) -> AsyncIterator[MixCache]:
    await run_in_threadpool(_remove_abandoned_mixes, cache_dir)
    with TemporaryDirectory(prefix="recording-mixes-", dir=cache_dir) as directory:
        cache = MixCache(Path(directory))

        async def prune_periodically() -> None:
            while True:
                await asyncio.sleep(30)
                await run_in_threadpool(cache.prune)

        pruner = asyncio.create_task(prune_periodically(), name="coverlab-recording-mix-pruner")
        try:
            yield cache
        finally:
            pruner.cancel()
            await asyncio.gather(pruner, return_exceptions=True)


def verified_stems(project_id: UUID, request: Request) -> tuple[_VerifiedStem, _VerifiedStem]:
    with request.app.state.session_factory() as session:
        statement = (
            select(Artifact)
            .join(AnalysisJob, AnalysisJob.id == Artifact.analysis_id)
            .where(
                Artifact.project_id == project_id,
                AnalysisJob.project_id == project_id,
                AnalysisJob.status == AnalysisStatus.SUCCEEDED,
                Artifact.kind.in_(("instrumental", "vocals")),
            )
            .order_by(Artifact.published_at.desc(), Artifact.id)
        )
        candidates: dict[str, Artifact] = {}
        for item in session.scalars(statement):
            if item.kind not in candidates and is_current_artifact(item):
                candidates[item.kind] = item
                if len(candidates) == 2:
                    break
        backing, vocal = candidates.get("instrumental"), candidates.get("vocals")
        if (
            backing is None
            or vocal is None
            or backing.analysis_id != vocal.analysis_id
            or backing.stage_id != vocal.stage_id
        ):
            raise LocalEngineError(
                status_code=409,
                code="recording_mix_stems_required",
                message="请先生成同一批次的伴奏和人声。",
            )
        try:
            backing_path = artifact_path(backing, backing.project)
            vocal_path = artifact_path(vocal, vocal.project)
            verified_backing = _VerifiedStem(backing_path, _file_snapshot(backing_path))
            verified_vocals = _VerifiedStem(vocal_path, _file_snapshot(vocal_path))
            backing_hash, vocal_hash = backing.sha256, vocal.sha256
        except (OSError, LocalEngineError) as error:
            raise LocalEngineError(
                status_code=409,
                code="recording_mix_stems_changed",
                message="分离音轨已变更，请重新生成。",
            ) from error
    try:
        if hash_file(backing_path) != backing_hash or hash_file(vocal_path) != vocal_hash:
            raise ValueError("Stem digest changed")
    except (OSError, ValueError) as error:
        raise LocalEngineError(
            status_code=409,
            code="recording_mix_stems_changed",
            message="分离音轨已变更，请重新生成。",
        ) from error
    return verified_backing, verified_vocals


def _build_mix(project_id: UUID, request: Request, payload: MixRequest) -> dict[str, str | float]:
    cache: MixCache = request.app.state.recording_mix_cache
    if not cache.build_slot.acquire(blocking=False):
        raise LocalEngineError(
            status_code=429, code="recording_mix_busy", message="正在生成混音，请稍后重试。"
        )
    mix_id = uuid4()
    target = cache.path(mix_id)
    published = False
    try:
        _, _, source_duration = project_context(project_id, request)
        ranges = validate_recording_ranges(payload.ranges, source_duration + 0.1)
        expected_duration = math.fsum(
            item["end"] - item["start"] for item in ranges
        ) + payload.preparation_seconds * len(ranges)
        if expected_duration > MAX_MIX_SECONDS:
            raise ValueError("Recording mix exceeds duration limit")
        backing, vocals = verified_stems(project_id, request)
        # Each rounded range adds less than one sample of rounding error.
        cache.reserve(
            (math.ceil(expected_duration * MIX_RATE) + len(ranges)) * MIX_FRAME_BYTES + 4096
        )
        duration = render_recording_mix(
            backing.path,
            vocals.path,
            target,
            ranges=ranges,
            preparation_seconds=payload.preparation_seconds,
            preparation_audio=payload.preparation_audio,
            vocal_percent=payload.vocal_percent,
        )
        backing.verify_unchanged()
        vocals.verify_unchanged()
        url = cache.publish(mix_id, target.stat().st_size)
        published = True
        return {"contentUrl": url, "durationSeconds": duration}
    except (ValueError, OverflowError, OSError, RuntimeError, MediaToolError) as error:
        raise LocalEngineError(
            status_code=422,
            code="recording_mix_invalid",
            message="无法生成录制混音，请检查片段和分离音轨。",
        ) from error
    finally:
        try:
            if not published:
                target.unlink(missing_ok=True)
        finally:
            cache.build_slot.release()


@mix_router.post("/projects/{project_id}/recording-mix", status_code=201)
async def create_mix(project_id: UUID, request: Request) -> dict[str, str | float]:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_SELECTION_QUERY_BYTES:
            raise LocalEngineError(
                status_code=413, code="recording_mix_invalid", message="录制混音参数过多。"
            )
        raw.extend(chunk)
    try:
        payload = MixRequest.model_validate_json(raw)
    except ValidationError as error:
        raise LocalEngineError(
            status_code=422, code="recording_mix_invalid", message="录制混音参数无效。"
        ) from error
    return await run_in_threadpool(_build_mix, project_id, request, payload)


class _MixResponse(FileResponse):
    def __init__(self, cache: MixCache, mix_id: UUID, path: Path) -> None:
        super().__init__(
            path,
            media_type="audio/wav",
            headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
        )
        self._cache = cache
        self._mix_id = mix_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._cache.release(self._mix_id)


@mix_media_router.get("/mixes/{mix_id}")
@mix_media_router.head("/mixes/{mix_id}", include_in_schema=False)
def mix_content(mix_id: UUID, request: Request, expires: int, signature: str) -> FileResponse:
    cache: MixCache = request.app.state.recording_mix_cache
    return _MixResponse(cache, mix_id, cache.acquire(mix_id, expires, signature))
