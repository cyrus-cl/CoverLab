"""Timed lyrics through the public CoverLab gateway."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from ai4music_core.media.ffmpeg import FFmpegTools, MediaToolError
from ai4music_core.media.hashing import CancellationToken

from .provider import LyricsResult
from .review import annotate_review
from .timed import render_lrc, render_srt, validate_document

MODEL_ID = "qwen3-asr-flash-filetrans"
PUBLIC_GATEWAY = "https://coverlab-lyrics.real-audio-backend.workers.dev"
MAX_SECONDS = 480
_TICKET = re.compile(r"[a-f0-9]{32}")
_MAX_RESPONSE = 2 * 1024 * 1024 + 1024

_ERRORS = {
    "cloud_audio_invalid": "这段人声无法识别，请重新生成。",
    "cloud_audio_limit": "云端识别每首最多支持 8 分钟。",
    "cloud_limit": "云端识别额度已用完，请稍后再试。",
    "cloud_task_not_found": "云端任务已过期，请重新生成。",
    "cloud_submit_unknown": "云端任务提交超时，请重新生成。",
    "cloud_transcription_failed": "歌词识别失败，请重试。",
    "cloud_unavailable": "云端识别暂时不可用，请稍后再试。",
    "cloud_upstream": "云端识别暂时不可用，请稍后再试。",
}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _cloud_error(code: Any) -> MediaToolError:
    if not isinstance(code, str) or code not in _ERRORS:
        code = "cloud_unavailable"
    return MediaToolError(code, _ERRORS[code])


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: BinaryIO | None = None,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    try:
        with build_opener(_NoRedirects()).open(
            Request(url, data=data, headers={"User-Agent": "CoverLab/0.8", **(headers or {})}),
            timeout=40,
        ) as response:
            content: bytes = response.read(_MAX_RESPONSE + 1)
    except HTTPError as error:
        if error.code == 404 and allow_missing:
            return None
        try:
            code = json.loads(error.read(1024)).get("error")
        except (ValueError, AttributeError, OSError):
            code = "cloud_limit" if error.code == 429 else "cloud_unavailable"
        raise _cloud_error(code) from None
    except (URLError, OSError, ValueError):
        raise MediaToolError("cloud_network", "无法连接云端识别服务，请检查网络。") from None
    try:
        if len(content) <= _MAX_RESPONSE:
            result = json.loads(content)
            if isinstance(result, dict):
                return result
    except (ValueError, RecursionError):
        pass
    raise MediaToolError("cloud_response_invalid", "云端返回的数据无法读取。")


def _timestamp(value: Any) -> float:
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative timestamp in milliseconds")
    return value / 1000


def timed_document(result: dict[str, Any], duration: float) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    previous_end = 0.0
    for transcript in result["transcripts"]:
        if transcript.get("channel_id", 0) != 0:
            continue
        for sentence in transcript["sentences"]:
            start = min(duration, max(previous_end, _timestamp(sentence["begin_time"])))
            end = min(duration, _timestamp(sentence["end_time"]))
            text = sentence["text"].strip()
            if not text or end <= start:
                continue
            words = []
            previous_word_end = start
            for token in sentence.get("words") or []:
                word_start = max(previous_word_end, _timestamp(token["begin_time"]))
                word_end = min(end, _timestamp(token["end_time"]))
                if word_end > word_start:
                    words.append({"start": word_start, "end": word_end, "word": token["text"]})
                    previous_word_end = word_end
            segments.append({"start": start, "end": end, "text": text, "words": words})
            previous_end = end
    return annotate_review(
        validate_document(
            {
                "schemaVersion": 1,
                "durationSeconds": duration,
                "language": None,
                "segments": segments,
            }
        )
    )


class BailianProvider:
    def __init__(self, gateway: str = PUBLIC_GATEWAY) -> None:
        self._gateway = gateway.rstrip("/")

    def runtime_info(self) -> dict[str, Any]:
        return {
            "provider": "bailian",
            "cloud": True,
            "model_id": MODEL_ID,
            "revision": "cloud",
            "available": bool(self._gateway),
            "model_cached": False,
            "download_bytes": 0,
            "errorCode": None if self._gateway else "cloud_unavailable",
        }

    def prepare(self, cancellation: CancellationToken) -> dict[str, Any]:
        cancellation.checkpoint()
        if not self._gateway:
            raise _cloud_error("cloud_unavailable")
        health = _request(self._gateway + "/health")
        if not health or health.get("available") is not True or health.get("version") != 1:
            raise _cloud_error("cloud_unavailable")
        return self.runtime_info()

    def transcribe(
        self,
        source: Path,
        target: Path,
        *,
        duration: float,
        task_state: Path,
        cancellation: CancellationToken,
        on_event: Callable[[dict[str, Any]], None],
    ) -> LyricsResult:
        if not self._gateway:
            raise _cloud_error("cloud_unavailable")
        if not 0 < duration <= MAX_SECONDS:
            raise _cloud_error("cloud_audio_limit")
        cancellation.checkpoint()
        state = task_state.with_name("gateway-ticket.txt")
        if state.exists():
            ticket = state.read_text(encoding="ascii").strip()
            if not _TICKET.fullmatch(ticket) or time.time() - state.stat().st_mtime >= 23 * 3600:
                raise _cloud_error("cloud_task_not_found")
            status = _request(f"{self._gateway}/v1/lyrics/{ticket}", allow_missing=True)
        else:
            ticket = uuid4().hex
            state.parent.mkdir(parents=True, exist_ok=True)
            pending = state.with_suffix(".part")
            pending.write_text(ticket, encoding="ascii")
            pending.replace(state)
            status = None
        if status is None:
            on_event({"completed": 0, "total": 2, "unit": "steps", "phase": "cloud_upload"})
            with TemporaryDirectory(prefix="coverlab-vocals-") as folder:
                audio = Path(folder) / "vocals.wav"
                FFmpegTools.discover().encode_cloud_audio(source, audio, cancellation=cancellation)
                cancellation.checkpoint()
                with audio.open("rb") as stream:
                    status = _request(
                        self._gateway + "/v1/lyrics",
                        headers={
                            "Content-Type": "audio/wav",
                            "Content-Length": str(audio.stat().st_size),
                            "Idempotency-Key": ticket,
                        },
                        data=stream,
                    )
        on_event({"completed": 1, "total": 2, "unit": "steps", "phase": "cloud_transcription"})
        deadline = time.monotonic() + 300
        while status and status.get("status") == "pending" and time.monotonic() < deadline:
            for _ in range(25):
                cancellation.checkpoint()
                time.sleep(0.1)
            status = _request(f"{self._gateway}/v1/lyrics/{ticket}")
        if not status or status.get("status") == "pending":
            raise MediaToolError("cloud_timeout", "云端仍在识别，重试可继续查询。")
        if status.get("status") != "succeeded":
            raise _cloud_error(status.get("error"))
        remote = status.get("result")
        if not isinstance(remote, dict):
            raise MediaToolError("cloud_response_invalid", "云端歌词格式异常。")
        try:
            document = timed_document(remote, duration)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise MediaToolError("cloud_response_invalid", "云端歌词时间戳格式异常。") from None
        cancellation.checkpoint()
        target.mkdir(parents=True, exist_ok=True)
        json_path, lrc, srt, raw = (
            target / f"lyrics.{ext}" for ext in ("json", "lrc", "srt", "raw.json")
        )
        json_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        lrc.write_text(render_lrc(document), encoding="utf-8")
        srt.write_text(render_srt(document), encoding="utf-8")
        raw.write_text(
            json.dumps({"transcripts": remote["transcripts"]}, ensure_ascii=False), encoding="utf-8"
        )
        return LyricsResult(json_path, lrc, srt, "cloud", MODEL_ID, "cloud", "", raw)
