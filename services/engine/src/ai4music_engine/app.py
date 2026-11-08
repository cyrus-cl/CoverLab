from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ai4music_core.config import Settings
from ai4music_core.db.session import create_database_engine, create_session_factory
from ai4music_core.jobs.processing import AudioStageExecutor
from ai4music_core.jobs.repository import JobRepository
from ai4music_core.jobs.runner import SingleJobRunner
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ai4music_engine.auth import origin_guard
from ai4music_engine.errors import LocalEngineError, local_engine_error_handler
from ai4music_engine.migrations import run_migrations
from ai4music_engine.routes import api_router
from ai4music_engine.routes.audio import playback_router
from ai4music_engine.routes.recording_mix import mix_media_router, recording_mix_cache


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_directories()
        run_migrations(settings.database_path)
        engine = create_database_engine(f"sqlite+pysqlite:///{settings.database_path.as_posix()}")
        session_factory = create_session_factory(engine)
        repository = JobRepository(session_factory)
        repository.recover_interrupted_jobs()
        runner = SingleJobRunner(
            repository, audio_executor=AudioStageExecutor(repository, model_dir=settings.model_dir)
        )
        app.state.session_factory = session_factory
        app.state.job_repository = repository
        app.state.job_runner = runner
        await runner.start()
        try:
            async with recording_mix_cache(settings.cache_dir) as mix_cache:
                app.state.recording_mix_cache = mix_cache
                yield
        finally:
            await runner.stop()
            engine.dispose()

    app = FastAPI(
        title="CoverLab Local Audio Engine",
        version="0.9.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-AI4Music-Token", "Last-Event-ID"],
    )
    app.middleware("http")(origin_guard(settings.allowed_origins))
    app.add_exception_handler(LocalEngineError, local_engine_error_handler)
    app.include_router(api_router)
    app.include_router(playback_router)
    app.include_router(mix_media_router)
    return app
