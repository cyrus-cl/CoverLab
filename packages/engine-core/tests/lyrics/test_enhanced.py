from __future__ import annotations

import itertools

import numpy as np
import pytest


def test_audio_chunks_cover_source_once_and_choose_quiet_boundary() -> None:
    from ai4music_core.lyrics.enhanced import audio_chunks

    samples = np.full(16000 * 65, 0.2, dtype=np.float32)
    samples[16000 * 24 : 16000 * 25] = 0
    chunks = list(audio_chunks(samples))
    assert chunks[0][0] == 0
    assert 24 * 16000 <= chunks[0][1] <= 25 * 16000
    assert chunks[-1][1] == len(samples)
    assert all(0 < end - start <= 28 * 16000 for start, end in chunks)
    assert all(left[1] == right[0] for left, right in itertools.pairwise(chunks))


def test_alignment_uses_native_times_and_exact_decoded_text() -> None:
    from ai4music_core.lyrics.enhanced import aligned_segments

    words = [
        {"start": 0.2, "end": 0.8, "word": "其余", "probability": 0.1},
        {"start": 0.8, "end": 1.2, "word": "你还爱我。", "probability": 0.8},
    ]
    lines = aligned_segments(words, "其余你还爱我。", offset=28.0, duration=2.0)
    assert lines[0]["text"] == "其余你还爱我。"
    assert lines[0]["words"][0] == {"start": 28.2, "end": 28.8, "word": "其余", "probability": 0.1}
    assert lines[0]["end"] == 29.2
    assert words[0]["start"] == 0.2


def test_alignment_cannot_rewrite_text_or_invent_overlapping_word_boundaries() -> None:
    from ai4music_core.lyrics.enhanced import aligned_segments

    with pytest.raises(ValueError, match="alignment_text_mismatch"):
        aligned_segments(
            [{"start": 0, "end": 1, "word": "different", "probability": 0.5}],
            "actual",
            offset=0,
            duration=2,
        )
    with pytest.raises(ValueError, match="unordered"):
        aligned_segments(
            [
                {"start": 0.5, "end": 1, "word": "a", "probability": 0.5},
                {"start": 0.9, "end": 1.5, "word": "b", "probability": 0.5},
            ],
            "ab",
            offset=0,
            duration=2,
        )


def test_chunk_pipeline_skips_silence_preserves_raw_and_reports_completed_audio() -> None:
    from ai4music_core.lyrics.enhanced import transcribe_chunks

    samples = np.zeros(16000 * 60, dtype=np.float32)
    samples[100:200] = 0.1
    calls = []
    progress = []

    def recognize(audio):
        calls.append(len(audio))
        return {
            "rawText": "language Chinese<asr_text>其余。",
            "language": "Chinese",
            "text": "其余。",
        }

    def align(audio, text, language):
        assert text == "其余。"
        assert language == "zh"
        return [{"start": 0.1, "end": 0.2, "word": text, "probability": 0.2}]

    document, raw = transcribe_chunks(
        samples,
        recognize=recognize,
        align=align,
        on_progress=lambda done, total: progress.append((done, total)),
    )
    assert len(calls) == 1
    assert document["segments"][0]["text"] == "其余。"
    assert raw[0]["recognition"]["rawText"].endswith("其余。")
    assert raw[0]["alignmentWords"][0]["start"] == 0.1
    assert progress[-1] == (60, 60)
    assert all(a[0] <= b[0] for a, b in itertools.pairwise(progress))
