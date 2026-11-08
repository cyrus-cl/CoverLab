from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable, Iterable
from typing import Annotated

from ai4music_core.config import Settings
from fastapi import Header, Request
from fastapi.responses import JSONResponse, Response

from ai4music_engine.errors import LocalEngineError

LOCAL_TOKEN_HEADER = "X-AI4Music-Token"


async def require_launch_token(
    request: Request,
    provided_token: Annotated[
        str | None,
        Header(alias=LOCAL_TOKEN_HEADER),
    ] = None,
) -> None:
    settings: Settings = request.app.state.settings
    expected_token = settings.launch_token.get_secret_value()
    if provided_token is None or not secrets.compare_digest(provided_token, expected_token):
        raise LocalEngineError(
            status_code=401,
            code="invalid_local_token",
            message="Local engine access denied",
        )


RequestHandler = Callable[[Request], Awaitable[Response]]
OriginMiddleware = Callable[[Request, RequestHandler], Awaitable[Response]]


def origin_guard(allowed_origins: Iterable[str]) -> OriginMiddleware:
    trusted_origins = frozenset(allowed_origins)

    async def middleware(request: Request, call_next: RequestHandler) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and origin not in trusted_origins:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "untrusted_origin",
                        "message": "Local engine origin denied",
                    }
                },
            )
        return await call_next(request)

    return middleware
