from __future__ import annotations

from fastapi import APIRouter, Depends

from ai4music_engine.auth import require_launch_token
from ai4music_engine.routes.audio import audio_router
from ai4music_engine.routes.events import events_router
from ai4music_engine.routes.health import health_router
from ai4music_engine.routes.jobs import jobs_router
from ai4music_engine.routes.project_outputs import outputs_router
from ai4music_engine.routes.projects import projects_router
from ai4music_engine.routes.recording_mix import mix_router

api_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_launch_token)],
)
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(jobs_router)
api_router.include_router(events_router)
api_router.include_router(audio_router)
api_router.include_router(outputs_router)
api_router.include_router(mix_router)

__all__ = ["api_router"]
