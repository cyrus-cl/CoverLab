"""Validated timed text. LRC retains line starts; JSON/SRT also retain ends."""
# All external document validation errors deliberately share ValueError so API
# callers have one stable exception contract regardless of the invalid field.
# ruff: noqa: TRY004

from __future__ import annotations

import math
import re
from typing import Any


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: expected a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field}: expected a finite nonnegative number")
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{field}: expected text without NUL")
    return value


def validate_document(document: Any) -> dict[str, Any]:
    """Return a detached schema-1 document or raise ValueError at its bad field."""
    if (
        not isinstance(document, dict)
        or type(document.get("schemaVersion")) is not int
        or document["schemaVersion"] != 1
    ):
        raise ValueError("schemaVersion: expected 1")
    duration = _number(document.get("durationSeconds"), "durationSeconds")
    language = document.get("language")
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ValueError("language: expected a language code or null")
    if not isinstance(document.get("segments"), list):
        raise ValueError("segments: expected an array")
    segments = []
    previous_end = 0.0
    for index, item in enumerate(document["segments"]):
        prefix = f"segments[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{prefix}: expected an object")
        start = _number(item.get("start"), f"{prefix}.start")
        end = _number(item.get("end"), f"{prefix}.end")
        if start < previous_end or end < start or end > duration:
            raise ValueError(f"{prefix}: unordered or out-of-audio boundaries")
        if not isinstance(item.get("words"), list):
            raise ValueError(f"{prefix}.words: expected an array")
        words = []
        previous_word_end = start
        for word_index, word in enumerate(item["words"]):
            field = f"{prefix}.words[{word_index}]"
            if not isinstance(word, dict):
                raise ValueError(f"{field}: expected an object")
            word_start = _number(word.get("start"), f"{field}.start")
            word_end = _number(word.get("end"), f"{field}.end")
            if word_start < previous_word_end or word_end < word_start or word_end > end:
                raise ValueError(f"{field}: unordered or out-of-segment boundaries")
            clean = {
                "start": word_start,
                "end": word_end,
                "word": _text(word.get("word"), f"{field}.word"),
            }
            if "probability" in word:
                probability = _number(word["probability"], f"{field}.probability")
                if probability > 1:
                    raise ValueError(f"{field}.probability: expected 0..1")
                clean["probability"] = probability
            words.append(clean)
            previous_word_end = word_end
        segments.append(
            {
                "start": start,
                "end": end,
                "text": _text(item.get("text"), f"{prefix}.text"),
                "words": words,
            }
        )
        if "review" in item:
            review = item["review"]
            if (
                not isinstance(review, dict)
                or type(review.get("required")) is not bool
                or type(review.get("excludeFromPrompt")) is not bool
                or not isinstance(review.get("reasons"), list)
                or not all(
                    isinstance(reason, str) and reason in {"repeated_text", "long_text"}
                    for reason in review["reasons"]
                )
            ):
                raise ValueError(f"{prefix}.review: invalid machine draft review metadata")
            segments[-1]["review"] = {
                "required": review["required"],
                "excludeFromPrompt": review["excludeFromPrompt"],
                "reasons": list(review["reasons"]),
            }
        previous_end = end
    return {
        "schemaVersion": 1,
        "durationSeconds": duration,
        "language": language,
        "segments": segments,
    }


_STAMP = re.compile(r"\[(\d+):(\d{2})(?:\.(\d{1,3}))?\]")
_METADATA = re.compile(r"\[([A-Za-z]+):(.*?)\]\s*")


def parse_lrc(
    text: str,
    duration_seconds: float,
    language: str | None = None,
) -> dict[str, Any]:
    """Import timed LRC lines; infer each end from the next start or audio end."""
    duration = _number(duration_seconds, "durationSeconds")
    entries: list[tuple[float, str]] = []
    offset = 0.0
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line:
            continue
        metadata = _METADATA.fullmatch(line)
        if metadata:
            if metadata[1].lower() == "offset":
                try:
                    offset = float(int(metadata[2])) / 1000
                except ValueError as exc:
                    raise ValueError("LRC: invalid offset") from exc
            continue
        stamps = []
        remaining = line
        while match := _STAMP.match(remaining):
            seconds = int(match[2])
            if seconds >= 60:
                raise ValueError("LRC: seconds must be less than 60")
            fraction = int((match[3] or "").ljust(3, "0")) / 1000
            stamps.append(int(match[1]) * 60 + seconds + fraction)
            remaining = remaining[match.end() :]
        if not stamps:
            raise ValueError("LRC: each lyric line needs a timestamp")
        entries.extend((stamp, remaining.strip()) for stamp in stamps)
    entries = sorted((round(start + offset, 6), line) for start, line in entries)
    segments = []
    for index, (start, line) in enumerate(entries):
        end = entries[index + 1][0] if index + 1 < len(entries) else duration
        if index and start == entries[index - 1][0]:
            raise ValueError("LRC: duplicate timestamps are ambiguous")
        segments.append({"start": start, "end": end, "text": line, "words": []})
    return validate_document(
        {
            "schemaVersion": 1,
            "durationSeconds": duration,
            "language": language,
            "segments": segments,
        }
    )


def _milliseconds(seconds: float) -> int:
    return math.floor(seconds * 1000 + 0.5)


def render_lrc(document: Any) -> str:
    lines = []
    for segment in validate_document(document)["segments"]:
        minutes, remainder = divmod(_milliseconds(segment["start"]), 60000)
        seconds, millis = divmod(remainder, 1000)
        # LRC is one line per timestamp; multiline edited text stays one line.
        text = " ".join(segment["text"].splitlines())
        lines.append(f"[{minutes:02}:{seconds:02}.{millis:03}]{text}\n")
    return "".join(lines)


def render_srt(document: Any) -> str:
    def stamp(seconds: float) -> str:
        hours, rest = divmod(_milliseconds(seconds), 3600000)
        minutes, rest = divmod(rest, 60000)
        secs, millis = divmod(rest, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    blocks = []
    for index, segment in enumerate(validate_document(document)["segments"], 1):
        blocks.append(
            f"{index}\n{stamp(segment['start'])} --> {stamp(segment['end'])}\n{segment['text']}\n"
        )
    return "\n".join(blocks)
