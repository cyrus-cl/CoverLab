from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import partial
from uuid import UUID

from ai4music_core.db.models import AnalysisStatus
from ai4music_core.jobs.processing import AudioStageExecutor
from ai4music_core.jobs.repository import (
    InvalidJobTransition,
    JobRepository,
    JobView,
)
from ai4music_core.lyrics import LyricsError
from ai4music_core.media.ffmpeg import MediaToolError
from ai4music_core.media.hashing import CancellationToken, JobCanceled, hash_file
from ai4music_core.separation import SeparationError


class SingleJobRunner:
    def __init__(
        self,
        repository: JobRepository,
        *,
        hash_chunk_size: int = 4 * 1024 * 1024,
        audio_executor: AudioStageExecutor | None = None,
    ) -> None:
        self._repository = repository
        self._hash_chunk_size = hash_chunk_size
        self._audio_executor = audio_executor
        self._queue: asyncio.Queue[UUID | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._cancellations: dict[UUID, CancellationToken] = {}
        self._active_stages: dict[UUID, UUID] = {}
        self._shutdown_requested = False

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._shutdown_requested = False
        self._worker_task = asyncio.create_task(
            self._consume(),
            name="ai4music-single-job-runner",
        )
        for job_id in self._repository.queued_job_ids():
            await self.enqueue(job_id)

    async def stop(self, *, timeout: float = 5.0) -> None:
        if self._worker_task is None:
            return
        self._shutdown_requested = True
        for cancellation in self._cancellations.values():
            cancellation.request()
        await self._queue.put(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=timeout)
        except TimeoutError:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task
            for job_id, stage_id in tuple(self._active_stages.items()):
                self._repository.interrupt_job(job_id, stage_id)
        finally:
            self._worker_task = None

    async def enqueue(self, job_id: UUID) -> None:
        await self._queue.put(job_id)

    async def cancel(self, job_id: UUID) -> JobView:
        job = self._repository.request_cancel(job_id)
        if cancellation := self._cancellations.get(job_id):
            cancellation.request()
        return job

    async def retry(self, job_id: UUID) -> JobView:
        job = self._repository.retry_job(job_id)
        await self.enqueue(job_id)
        return job

    def register_external(self, job_id: UUID, stage_id: UUID, token: CancellationToken) -> None:
        self._cancellations[job_id] = token
        self._active_stages[job_id] = stage_id

    def unregister_external(self, job_id: UUID) -> None:
        self._cancellations.pop(job_id, None)
        self._active_stages.pop(job_id, None)

    async def run_once(self, job_id: UUID) -> None:
        job = self._repository.require_job(job_id)
        if job.operation != "fingerprint":
            await self._run_processing(job_id)
            return
        try:
            claimed = self._repository.start_attempt(job.stage.id)
        except InvalidJobTransition:
            return

        cancellation = CancellationToken()
        self._cancellations[job_id] = cancellation
        self._active_stages[job_id] = claimed.stage_id
        try:
            digest = await asyncio.to_thread(
                hash_file,
                claimed.source_path,
                chunk_size=self._hash_chunk_size,
                cancellation=cancellation,
                on_progress=lambda done, total: self._record_progress(
                    claimed.id,
                    claimed.stage_id,
                    cancellation,
                    done,
                    total,
                ),
            )
            cancellation.checkpoint()
            stat = claimed.source_path.stat()
            if stat.st_size != claimed.source_size or stat.st_mtime_ns != claimed.source_mtime_ns:
                self._repository.fail_job(
                    claimed.id,
                    claimed.stage_id,
                    code="source_changed",
                    message="Source file changed during fingerprinting",
                )
                return
            current = self._repository.require_job(claimed.id)
            if current.status is AnalysisStatus.CANCELING:
                raise JobCanceled
            self._repository.complete_fingerprint(
                claimed.id,
                claimed.stage_id,
                digest=digest,
            )
        except JobCanceled:
            if self._shutdown_requested:
                self._repository.interrupt_job(claimed.id, claimed.stage_id)
            else:
                self._repository.mark_canceled(claimed.id, claimed.stage_id)
        except FileNotFoundError:
            self._repository.fail_job(
                claimed.id,
                claimed.stage_id,
                code="source_missing",
                message="Source file no longer exists",
            )
        except OSError:
            self._repository.fail_job(
                claimed.id,
                claimed.stage_id,
                code="source_unreadable",
                message="Source file could not be read",
            )
        # The background consumer must persist a stable failure instead of
        # terminating silently on an unexpected model or filesystem error.
        except Exception:  # noqa: BLE001
            self._repository.fail_job(
                claimed.id,
                claimed.stage_id,
                code="fingerprint_failed",
                message="Source fingerprint failed",
            )
        finally:
            self._cancellations.pop(job_id, None)
            self._active_stages.pop(job_id, None)

    async def _run_processing(self, job_id: UUID) -> None:
        cancellation = CancellationToken()
        self._cancellations[job_id] = cancellation
        current = self._repository.require_job(job_id)
        stage_id = current.stage.id
        try:
            while current.status not in {
                AnalysisStatus.SUCCEEDED,
                AnalysisStatus.FAILED,
                AnalysisStatus.CANCELED,
            }:
                stage_id = current.stage.id
                self._active_stages[job_id] = stage_id
                cancellation.checkpoint()
                if current.status is AnalysisStatus.CANCELING:
                    raise JobCanceled
                claimed = self._repository.start_attempt(stage_id)
                if current.stage.stage_name == "fingerprint_source":
                    digest = await asyncio.to_thread(
                        hash_file,
                        claimed.source_path,
                        cancellation=cancellation,
                        chunk_size=self._hash_chunk_size,
                        on_progress=partial(self._record_progress, job_id, stage_id, cancellation),
                    )
                    cancellation.checkpoint()
                    stat = claimed.source_path.stat()
                    if (
                        stat.st_size != claimed.source_size
                        or stat.st_mtime_ns != claimed.source_mtime_ns
                    ):
                        raise MediaToolError(
                            "source_changed", "Source file changed during fingerprinting"
                        )
                    self._repository.complete_fingerprint(job_id, stage_id, digest=digest)
                else:
                    if self._audio_executor is None:
                        raise MediaToolError(
                            "media_tools_missing", "Audio processing is unavailable"
                        )
                    await asyncio.to_thread(
                        self._audio_executor.execute,
                        claimed,
                        current.stage.stage_name,
                        cancellation,
                    )
                current = self._repository.require_job(job_id)
        except JobCanceled:
            if self._shutdown_requested:
                self._repository.interrupt_job(job_id, stage_id)
            else:
                self._repository.mark_canceled(job_id, stage_id)
        except InvalidJobTransition:
            # A queued task may have been canceled before the consumer claimed it.
            status = self._repository.require_job(job_id).status
            if status is AnalysisStatus.CANCELING:
                self._repository.mark_canceled(job_id, stage_id)
            elif status not in {AnalysisStatus.CANCELED, AnalysisStatus.SUCCEEDED}:
                self._repository.fail_job(
                    job_id,
                    stage_id,
                    code="processing_state_error",
                    message="Processing stage could not be started",
                )
        except MediaToolError as error:
            self._repository.fail_job(job_id, stage_id, code=error.code, message=error.message)
        except LyricsError:
            self._repository.fail_job(
                job_id,
                stage_id,
                code="lyrics_failed",
                message="Local lyric recognition failed; retry or import timed lyrics",
            )
        except SeparationError as error:
            code = str(error).split(":", 1)[0]
            if code not in {
                "runtime_missing",
                "model_checksum_mismatch",
                "model_download_failed",
                "model_download_incomplete",
            }:
                code = "separation_failed"
            self._repository.fail_job(
                job_id, stage_id, code=code, message="Local model processing failed"
            )
        except FileNotFoundError:
            self._repository.fail_job(
                job_id, stage_id, code="source_missing", message="Source file no longer exists"
            )
        except Exception:  # noqa: BLE001 - persist a bounded error at the worker boundary
            self._repository.fail_job(
                job_id, stage_id, code="processing_failed", message="Local audio processing failed"
            )
        finally:
            self._cancellations.pop(job_id, None)
            self._active_stages.pop(job_id, None)

    def _record_progress(
        self,
        job_id: UUID,
        stage_id: UUID,
        cancellation: CancellationToken,
        done: int,
        total: int,
    ) -> None:
        cancellation.checkpoint()
        self._repository.record_progress(
            job_id,
            stage_id,
            bytes_done=done,
            bytes_total=total,
        )

    async def _consume(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                if job_id is None:
                    return
                if self._shutdown_requested:
                    continue
                await self.run_once(job_id)
            finally:
                self._queue.task_done()
