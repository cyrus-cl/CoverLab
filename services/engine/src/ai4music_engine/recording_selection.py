from __future__ import annotations

import math
from typing import Annotated, Any

from ai4music_core.media.recording_mix import PreparationAudio
from pydantic import BaseModel, ConfigDict, Field

MAX_SELECTION_RANGES = 500
MAX_SELECTION_QUERY_BYTES = 32 * 1024


class MixSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preparation_seconds: Annotated[int, Field(alias="preparationSeconds", strict=True, ge=0, le=10)]
    preparation_audio: Annotated[PreparationAudio, Field(alias="preparationAudio")]
    vocal_percent: Annotated[int, Field(alias="vocalPercent", strict=True, ge=0, le=100)]


class MixRequest(MixSettings):
    ranges: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=MAX_SELECTION_RANGES)]


class SavedMixSettings(MixSettings):
    include_backing: Annotated[bool, Field(alias="includeBacking", strict=True)]


def validate_recording_ranges(document: Any, duration: float) -> list[dict[str, float]]:
    if not isinstance(document, list) or not 1 <= len(document) <= MAX_SELECTION_RANGES:
        raise ValueError("Invalid selection count")
    ranges: list[dict[str, float]] = []
    previous_end = 0.0
    for item in document:
        if not isinstance(item, dict) or set(item) != {"start", "end"}:
            raise ValueError("Invalid selection range")
        if type(item["start"]) not in {int, float} or type(item["end"]) not in {int, float}:
            raise ValueError("Selection timestamps must be numbers")
        start, end = float(item["start"]), float(item["end"])
        if not (
            math.isfinite(start) and math.isfinite(end) and previous_end <= start < end <= duration
        ):
            raise ValueError("Selection ranges overlap or exceed source duration")
        ranges.append({"start": start, "end": end})
        previous_end = end
    return ranges
