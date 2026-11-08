from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class LocalEngineError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


async def local_engine_error_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, LocalEngineError):
        raise error
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
    )
