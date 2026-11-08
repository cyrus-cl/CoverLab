"""Bounded audio-grounded recognition followed by native CT2 text alignment.

There are no reference lyrics or semantic corrections in this pipeline. The
recognizer owns text; the aligner can only attach measured attention/DTW times.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

from .timed import validate_document

SAMPLE_RATE = 16000
LANGUAGES = {
    "chinese": "zh",
    "english": "en",
    "cantonese": "yue",
    "arabic": "ar",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "portuguese": "pt",
    "indonesian": "id",
    "italian": "it",
    "korean": "ko",
    "russian": "ru",
    "thai": "th",
    "vietnamese": "vi",
    "japanese": "ja",
    "turkish": "tr",
    "hindi": "hi",
    "malay": "ms",
    "dutch": "nl",
    "swedish": "sv",
    "danish": "da",
    "finnish": "fi",
    "polish": "pl",
    "czech": "cs",
    "filipino": "tl",
    "persian": "fa",
    "greek": "el",
    "hungarian": "hu",
    "macedonian": "mk",
    "romanian": "ro",
}


def audio_chunks(audio: Any) -> Iterator[tuple[int, int]]:
    """Cover samples once, preferring a quiet 400ms area between 20 and 28s."""
    import numpy as np

    start = 0
    maximum = 28 * SAMPLE_RATE
    minimum = 20 * SAMPLE_RATE
    radius = SAMPLE_RATE // 5
    stride = SAMPLE_RATE // 10
    while start < len(audio):
        end = min(start + maximum, len(audio))
        if end < len(audio):
            candidates = range(start + minimum, end - radius + 1, stride)
            # This only chooses an audio cut, never a word or a replacement text.
            end = min(
                candidates, key=lambda p: float(np.mean(np.square(audio[p - radius : p + radius])))
            )
        yield start, end
        start = end


def aligned_segments(
    words: list[dict[str, Any]], text: str, *, offset: float, duration: float
) -> list[dict[str, Any]]:
    if "".join(str(word["word"]) for word in words) != text:
        raise ValueError("alignment_text_mismatch: native alignment must preserve recognized text")
    lines: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        # Native CT2 uses a 20ms grid; only the padded final frame may exceed
        # the known chunk length. Preserve its original bounds in raw output.
        start, end = float(word["start"]), float(word["end"])
        if not math.isfinite(start + end) or start < 0 or end > duration + 0.021:
            raise ValueError("alignment_out_of_audio: native word is outside the audio chunk")
        current.append(
            {
                "start": offset + min(start, duration),
                "end": offset + min(end, duration),
                "word": str(word["word"]),
                "probability": float(word["probability"]),
            }
        )
        combined = "".join(item["word"] for item in current)
        if re.search(r"[，。！？；,.!?;]$", combined) or len(combined) >= 28:
            lines.append(
                {
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                    "text": combined.strip(),
                    "words": current,
                }
            )
            current = []
    if current:
        lines.append(
            {
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": "".join(word["word"] for word in current).strip(),
                "words": current,
            }
        )
    validated = validate_document(
        {
            "schemaVersion": 1,
            "durationSeconds": offset + duration,
            "language": None,
            "segments": lines,
        }
    )
    return cast(list[dict[str, Any]], validated["segments"])


def transcribe_chunks(
    audio: Any,
    *,
    recognize: Callable[[Any], dict[str, Any]],
    align: Callable[[Any, str, str], list[dict[str, Any]]],
    on_progress: Callable[[float, float], None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import numpy as np

    duration = len(audio) / SAMPLE_RATE
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "durationSeconds": duration,
        "language": None,
        "segments": [],
    }
    raw = []
    for start, end in audio_chunks(audio):
        chunk = audio[start:end]
        record: dict[str, Any] = {
            "start": start / SAMPLE_RATE,
            "end": end / SAMPLE_RATE,
            "recognition": None,
            "alignmentWords": [],
        }
        if np.any(chunk):
            recognized = recognize(chunk)
            record["recognition"] = recognized
            text = str(recognized["text"])
            if text.strip():
                language = LANGUAGES.get(str(recognized.get("language", "")).lower())
                if language is None:
                    raise ValueError(
                        "alignment_language_unsupported: recognizer did not identify a supported language"
                    )
                if document["language"] is None:
                    document["language"] = language
                words = align(chunk, text, language)
                record["alignmentWords"] = words
                document["segments"].extend(
                    aligned_segments(
                        words,
                        text,
                        offset=start / SAMPLE_RATE,
                        duration=(end - start) / SAMPLE_RATE,
                    )
                )
        raw.append(record)
        on_progress(end / SAMPLE_RATE, duration)
    return validate_document(document), raw


def infer_runtime(
    model_path: Path,
    alignment_path: Path,
    audio: Any,
    *,
    emit: Callable[..., None],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load pinned local CPU models; parent cancellation terminates this child."""
    import os

    import torch
    from faster_whisper import WhisperModel
    from faster_whisper.audio import pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    from transformers import AutoProcessor
    from transformers.models.qwen3_asr.modeling_qwen3_asr import Qwen3ASRForConditionalGeneration

    threads = max(1, min(4, os.cpu_count() or 1))
    torch.set_num_threads(threads)
    processor_factory: Any = AutoProcessor
    processor = processor_factory.from_pretrained(str(model_path), local_files_only=True)
    recognizer_factory: Any = Qwen3ASRForConditionalGeneration
    recognizer = recognizer_factory.from_pretrained(
        str(model_path),
        local_files_only=True,
        dtype=torch.float32,
        attn_implementation="sdpa",
    )
    recognizer.eval()
    aligner = WhisperModel(
        str(alignment_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
        num_workers=1,
        local_files_only=True,
    )
    emit("phase", phase="transcription", device="cpu")

    def recognize(chunk: Any) -> dict[str, Any]:
        # NumPy input is already decoded/resampled by our validated local media
        # path. No librosa, external audio URL, reference text or system prompt.
        inputs = processor.apply_transcription_request(audio=chunk, language=None)
        inputs = inputs.to("cpu", torch.float32)
        with torch.inference_mode():
            output_ids = recognizer.generate(**inputs, max_new_tokens=384, do_sample=False)
        generated = output_ids[:, inputs["input_ids"].shape[1] :]
        eos_ids = recognizer.config.eos_token_id
        eos_ids = [eos_ids] if isinstance(eos_ids, int) else eos_ids
        if generated.shape[1] >= 384 and int(generated[0, -1]) not in eos_ids:
            raise ValueError("recognition_truncated: chunk output exceeded the bounded decoder")
        parsed = processor.decode(generated, return_format="parsed")[0]
        return {
            "rawText": processor.decode(generated)[0],
            "language": parsed["language"],
            "text": parsed["transcription"],
        }

    def align(chunk: Any, text: str, language: str) -> list[dict[str, Any]]:
        emit("phase", phase="alignment", device="cpu")
        tokenizer = Tokenizer(
            aligner.hf_tokenizer,
            aligner.model.is_multilingual,
            task="transcribe",
            language=language,
        )
        text_tokens = tokenizer.encode(text)
        features = aligner.feature_extractor(chunk)
        frames = min(features.shape[-1] - 1, aligner.feature_extractor.nb_max_frames)
        encoded = aligner.encode(pad_or_trim(features))
        words = aligner.find_alignment(
            tokenizer, [text_tokens], encoded, frames, median_filter_width=7
        )[0]
        return [
            {
                "start": float(w["start"]),
                "end": float(w["end"]),
                "word": str(w["word"]),
                "probability": float(w["probability"]),
                "tokens": list(w["tokens"]),
            }
            for w in words
        ]

    return transcribe_chunks(
        audio,
        recognize=recognize,
        align=align,
        on_progress=lambda done, total: emit(
            "progress",
            phase="transcription",
            completed=done,
            total=total,
            unit="seconds",
            device="cpu",
        ),
    )
