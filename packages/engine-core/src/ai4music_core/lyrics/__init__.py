"""Local multilingual timed transcription; importing this package loads no ML runtime."""

from .provider import LyricsError, LyricsResult, WhisperProvider
from .timed import parse_lrc, render_lrc, render_srt, validate_document

__all__ = [
    "LyricsError",
    "LyricsResult",
    "WhisperProvider",
    "parse_lrc",
    "render_lrc",
    "render_srt",
    "validate_document",
]
