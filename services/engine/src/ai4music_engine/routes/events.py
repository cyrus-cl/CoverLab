from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated
from uuid import UUID

from ai4music_core.jobs.repository import EventView, JobNotFound, JobRepository
from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from ai4music_engine.errors import LocalEngineError
from ai4music_engine.routes.jobs import JobRepositoryDependency
from ai4music_engine.serialization import utc_isoformat

events_router = APIRouter(prefix="/analyses", tags=["events"])


def _encode_event(event: EventView) -> str:
    data = json.dumps(
        {
            "sequence": event.sequence,
            "type": event.event_type,
            "message": event.message,
            "payload": event.payload,
            "createdAt": utc_isoformat(event.created_at),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


async def _event_stream(
    request: Request,
    repository: JobRepository,
    analysis_id: UUID,
    after_sequence: int,
) -> AsyncIterator[str]:
    cursor = after_sequence
    last_heartbeat = monotonic()
    while True:
        try:
            events = await asyncio.to_thread(repository.events_after, analysis_id, cursor)
        except JobNotFound:
            return
        for event in events:
            cursor = event.sequence
            yield _encode_event(event)
        if await asyncio.to_thread(repository.is_terminal, analysis_id) and not events:
            return
        if await request.is_disconnected():
            return
        now = monotonic()
        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ": heartbeat\n\n"
        await asyncio.sleep(0.25)


@events_router.get("/{analysis_id}/events")
async def stream_events(
    analysis_id: UUID,
    request: Request,
    repository: JobRepositoryDependency,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        repository.require_job(analysis_id)
    except JobNotFound as error:
        raise LocalEngineError(
            status_code=404,
            code="analysis_not_found",
            message="Analysis not found",
        ) from error
    return StreamingResponse(
        _event_stream(request, repository, analysis_id, last_event_id or 0),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
