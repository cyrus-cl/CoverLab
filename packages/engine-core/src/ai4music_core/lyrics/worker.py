"""CPU-only inference child. Frozen engine dispatches --lyrics-worker to main."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from ai4music_core.media.hashing import CancellationToken

from .models import DEFAULT_MODEL, QWEN_MODEL, SMALL_MODEL, SUPPORTED_MODELS, verify_model
from .timed import validate_document

TRANSCRIPTION_OPTIONS = {
    "task": "transcribe",
    "beam_size": 5,
    "word_timestamps": True,
    # Full-song comparisons improved CER with audio-grounded previous decoding.
    # Leave language detection automatic; a Chinese hint gave identical text.
    "condition_on_previous_text": True,
    "language": None,
    "vad_filter": False,
    "temperature": 0,
    "hallucination_silence_threshold": 2.0,
}


def infer(
    model_path: Path,
    source: Path,
    protocol: TextIO,
    *,
    model_id: str = DEFAULT_MODEL.model_id,
    alignment_path: Path | None = None,
) -> None:
    spec = SUPPORTED_MODELS.get(model_id)
    if spec is None:
        raise ValueError("model_unsupported: choose a curated pinned model")

    def emit(event: str, **fields: Any) -> None:
        protocol.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")
        protocol.flush()

    # Verify the actual files in the child immediately before model loading too.
    emit("phase", phase="model_verify")
    verify_model(model_path, spec, CancellationToken())
    if spec == QWEN_MODEL:
        if alignment_path is None:
            raise ValueError(
                "alignment_model_missing: provide the pinned local small alignment model"
            )
        verify_model(alignment_path, SMALL_MODEL, CancellationToken())
    emit("phase", phase="audio_load")
    import numpy as np
    import soundfile as sf
    from faster_whisper.audio import decode_audio

    # Parent passes a normalized WAV. Check it before PyAV conversion, which can
    # conceal nonfinite float samples by converting them to integer zero.
    with sf.SoundFile(source) as original:
        if not original.frames or original.samplerate <= 0:
            raise ValueError("audio_invalid: empty audio")
        duration = original.frames / original.samplerate
        for block in original.blocks(blocksize=65536, dtype="float32", always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError("audio_invalid: nonfinite samples")
    audio = decode_audio(str(source), sampling_rate=16000)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("audio_invalid: empty or nonfinite decoded audio")

    document: dict[str, Any] = {
        "schemaVersion": 1,
        "durationSeconds": duration,
        "language": None,
        "segments": [],
    }
    raw_recognition: dict[str, Any] = {
        "provider": "faster-whisper",
        "model_id": spec.model_id,
        "revision": spec.revision,
        "model_sha256": spec.sha256,
        "decoding": dict(TRANSCRIPTION_OPTIONS),
        "durationSeconds": duration,
        "language": None,
        "segments": [],
    }
    # Deterministic exact silence has no recognized content. Do not ask a
    # generative recognizer to hallucinate subtitles for a zero signal.
    if spec == QWEN_MODEL:
        raw_recognition.update(
            {
                "provider": "qwen3-asr+whisper-alignment",
                "alignment_model_id": SMALL_MODEL.model_id,
                "alignment_revision": SMALL_MODEL.revision,
                "alignment_model_sha256": SMALL_MODEL.sha256,
                "probability_source": "Whisper forced-alignment token likelihood; not ASR correctness",
                "decoding": {
                    "chunk_max_seconds": 28,
                    "boundary": "quietest400ms_between20and28s",
                    "language": None,
                    "max_new_tokens": 384,
                    "do_sample": False,
                    "reference_text_supplied": False,
                },
                "chunks": [],
            }
        )
        if np.any(audio):
            from .enhanced import infer_runtime

            emit("phase", phase="runtime_load", device="cpu")
            assert alignment_path is not None
            document, chunks = infer_runtime(model_path, alignment_path, audio, emit=emit)
            # Resampling can round source duration by one sample. Retain the
            # source container's known duration and bound the padded final frame.
            document["durationSeconds"] = duration
            for segment in document["segments"]:
                segment["start"] = min(segment["start"], duration)
                segment["end"] = min(segment["end"], duration)
                for word in segment["words"]:
                    word["start"] = min(word["start"], duration)
                    word["end"] = min(word["end"], duration)
            raw_recognition["chunks"] = chunks
            raw_recognition["language"] = document["language"]
    elif np.any(audio):
        emit("phase", phase="runtime_load", device="cpu")
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(model_path),
            device="cpu",
            compute_type="int8",
            cpu_threads=max(1, min(4, os.cpu_count() or 1)),
            num_workers=1,
            local_files_only=True,
        )
        emit("phase", phase="transcription", device="cpu")
        segments, info = model.transcribe(audio, **TRANSCRIPTION_OPTIONS)
        document["language"] = info.language
        raw_recognition["language"] = info.language
        for segment in segments:
            # Preserve native text, boundaries and diagnostics before creating
            # the validated editor/export document. No semantic rewriting.
            raw_recognition["segments"].append(
                {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": str(segment.text),
                    "avg_logprob": getattr(segment, "avg_logprob", None),
                    "no_speech_prob": getattr(segment, "no_speech_prob", None),
                    "compression_ratio": getattr(segment, "compression_ratio", None),
                    "words": [
                        {
                            "start": float(w.start),
                            "end": float(w.end),
                            "word": str(w.word),
                            "probability": float(w.probability),
                        }
                        for w in segment.words or []
                    ],
                }
            )
            # The decoder can place a final timestamp in its padded last frame.
            # Bound it by the known source duration; retain the model's text.
            start = min(float(segment.start), duration)
            end = min(float(segment.end), duration)
            words = []
            for word in segment.words or []:
                words.append(
                    {
                        "start": min(float(word.start), duration),
                        "end": min(float(word.end), duration),
                        "word": str(word.word),
                        "probability": float(word.probability),
                    }
                )
            # Whisper's coarse sentence metadata can exclude its own decoded
            # word timestamps, especially for singing. Keep every word bound
            # unchanged and make the sentence contain the actual word envelope.
            # Strict validation below still rejects truly unordered words or
            # overlapping sentences instead of inventing replacement timings.
            if words:
                start = min(start, *(word["start"] for word in words))
                end = max(end, *(word["end"] for word in words))
            document["segments"].append(
                {
                    "start": start,
                    "end": end,
                    "text": segment.text.strip(),
                    "words": words,
                }
            )
            # Progress follows the completed segment's audio boundary, with no
            # advancing timer while native inference is busy.
            emit(
                "progress",
                phase="transcription",
                completed=min(end, duration),
                total=duration,
                unit="seconds",
                device="cpu",
            )
    from .review import annotate_review

    document = annotate_review(validate_document(document))
    raw_recognition["review"] = [
        {"segment": index, **segment["review"]}
        for index, segment in enumerate(document["segments"])
        if "review" in segment
    ]
    emit(
        "result",
        device="cpu",
        model_id=spec.model_id,
        revision=spec.revision,
        model_sha256=spec.sha256,
        raw_recognition=raw_recognition,
        document=validate_document(document),
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CoverLab local timed transcription worker")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model-id", choices=tuple(SUPPORTED_MODELS))
    parser.add_argument("--alignment-model", type=Path)
    args = parser.parse_args(arguments)
    protocol = sys.stdout
    reconfigure = getattr(protocol, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if args.model_id or args.alignment_model:
                infer(
                    args.model,
                    args.source,
                    protocol,
                    model_id=args.model_id or DEFAULT_MODEL.model_id,
                    alignment_path=args.alignment_model,
                )
            else:
                infer(args.model, args.source, protocol)
        return 0
    except ValueError as exc:
        # Validators own these bounded diagnostic messages, never arbitrary
        # native-library logs or user paths.
        code = str(exc).split(":", 1)[0][:80]
    except (ImportError, OSError):
        code = "runtime_unavailable: verify the local CPU transcription runtime and WAV"
    except Exception:  # noqa: BLE001 - subprocess protocol is the error boundary
        code = "inference_failed: local transcription could not complete"
    protocol.write(json.dumps({"event": "error", "code": code}) + "\n")
    protocol.flush()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
