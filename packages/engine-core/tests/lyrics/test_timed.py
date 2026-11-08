from __future__ import annotations

import copy
import math

import pytest


def document() -> dict:
    return {
        "schemaVersion": 1,
        "durationSeconds": 4.5,
        "language": "zh",
        "segments": [
            {
                "start": 0.125,
                "end": 1.5,
                "text": "你好",
                "words": [{"start": 0.125, "end": 1.5, "word": "你好", "probability": 0.8}],
            },
            {"start": 2.25, "end": 4.5, "text": "世界", "words": []},
        ],
    }


def test_validate_document_preserves_valid_words_and_does_not_mutate() -> None:
    from ai4music_core.lyrics.timed import validate_document

    source = document()
    original = copy.deepcopy(source)
    assert validate_document(source) == source
    assert source == original


@pytest.mark.parametrize("value", [math.nan, math.inf, -1, True, "1"])
def test_rejects_nonfinite_or_invalid_duration(value: object) -> None:
    from ai4music_core.lyrics.timed import validate_document

    source = document()
    source["durationSeconds"] = value
    with pytest.raises(ValueError, match="durationSeconds"):
        validate_document(source)


@pytest.mark.parametrize(
    "change", ["reversed", "overlap", "out_of_audio", "word_outside", "probability"]
)
def test_rejects_invalid_segment_and_word_boundaries(change: str) -> None:
    from ai4music_core.lyrics.timed import validate_document

    source = document()
    if change == "reversed":
        source["segments"][0]["end"] = 0.1
    elif change == "overlap":
        source["segments"][1]["start"] = 1.0
    elif change == "out_of_audio":
        source["segments"][1]["end"] = 4.6
    elif change == "word_outside":
        source["segments"][0]["words"][0]["end"] = 2
    else:
        source["segments"][0]["words"][0]["probability"] = 1.1
    with pytest.raises(ValueError):
        validate_document(source)


def test_empty_recognition_is_valid_and_exports_empty_text() -> None:
    from ai4music_core.lyrics.timed import render_lrc, render_srt, validate_document

    source = {"schemaVersion": 1, "durationSeconds": 2, "language": None, "segments": []}
    assert validate_document(source) == source
    assert render_lrc(source) == ""
    assert render_srt(source) == ""


def test_schema_version_true_is_not_integer_version_one() -> None:
    from ai4music_core.lyrics.timed import validate_document

    source = document()
    source["schemaVersion"] = True
    with pytest.raises(ValueError, match="schemaVersion"):
        validate_document(source)


def test_lrc_roundtrip_preserves_text_and_millisecond_starts() -> None:
    from ai4music_core.lyrics.timed import parse_lrc, render_lrc

    source = document()
    rendered = render_lrc(source)
    assert rendered == "[00:00.125]你好\n[00:02.250]世界\n"
    restored = parse_lrc(rendered, 4.5, language="zh")
    assert [(s["start"], s["text"]) for s in restored["segments"]] == [
        (0.125, "你好"),
        (2.25, "世界"),
    ]
    assert restored["segments"][0]["end"] == 2.25
    assert restored["segments"][-1]["end"] == 4.5


def test_lrc_handles_metadata_multiple_timestamps_and_offset() -> None:
    from ai4music_core.lyrics.timed import parse_lrc

    source = parse_lrc("[ti:Example]\n[offset:100]\n[00:01.20][00:03.400]line\n", 5)
    assert [s["start"] for s in source["segments"]] == [1.3, 3.5]
    assert [s["end"] for s in source["segments"]] == [3.5, 5]


@pytest.mark.parametrize(
    "text", ["untimed lyric", "[00:60.0]bad", "[00:06.0]past end", "[00:01]a\n[00:01]b"]
)
def test_lrc_rejects_ambiguous_or_invalid_lines(text: str) -> None:
    from ai4music_core.lyrics.timed import parse_lrc

    with pytest.raises(ValueError):
        parse_lrc(text, 5)


def test_srt_exports_actual_start_and_end_with_millisecond_precision() -> None:
    from ai4music_core.lyrics.timed import render_srt

    assert render_srt(document()) == (
        "1\n00:00:00,125 --> 00:00:01,500\n你好\n\n2\n00:00:02,250 --> 00:00:04,500\n世界\n"
    )
