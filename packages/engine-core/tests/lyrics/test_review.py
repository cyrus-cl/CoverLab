from __future__ import annotations

import pytest


def test_only_abnormal_intraline_loop_is_flagged_and_raw_text_survives() -> None:
    from ai4music_core.lyrics.review import annotate_review
    from ai4music_core.lyrics.timed import validate_document

    text = "作词 " + "作曲 " * 40
    doc = {
        "schemaVersion": 1,
        "durationSeconds": 4,
        "language": "zh",
        "segments": [{"start": 0, "end": 4, "text": text, "words": []}],
    }
    result = validate_document(annotate_review(doc))
    assert result["segments"][0]["text"] == text
    assert result["segments"][0]["review"] == {
        "required": True,
        "reasons": ["repeated_text"],
        "excludeFromPrompt": True,
    }
    assert "review" not in doc["segments"][0]


def test_normal_repeated_chorus_on_separate_lines_stays_available() -> None:
    from ai4music_core.lyrics.review import annotate_review

    doc = {
        "schemaVersion": 1,
        "durationSeconds": 9,
        "language": "zh",
        "segments": [
            {"start": i * 3, "end": i * 3 + 2, "text": "我爱你我爱你我爱你", "words": []}
            for i in range(3)
        ],
    }
    assert annotate_review(doc) == doc


def test_excessive_machine_line_is_marked_without_truncating_text() -> None:
    from ai4music_core.lyrics.review import annotate_review

    text = "".join(chr(0x4E00 + i) for i in range(241))
    doc = {
        "schemaVersion": 1,
        "durationSeconds": 3,
        "language": "zh",
        "segments": [{"start": 0, "end": 3, "text": text, "words": []}],
    }
    result = annotate_review(doc)
    assert result["segments"][0]["text"] == text
    assert result["segments"][0]["review"]["excludeFromPrompt"] is True
    assert result["segments"][0]["review"]["reasons"] == ["long_text"]


def test_malformed_external_review_metadata_uses_validation_error_contract() -> None:
    from ai4music_core.lyrics.timed import validate_document

    doc = {
        "schemaVersion": 1,
        "durationSeconds": 2,
        "language": None,
        "segments": [
            {
                "start": 0,
                "end": 1,
                "text": "line",
                "words": [],
                "review": {"required": True, "excludeFromPrompt": True, "reasons": [{}]},
            }
        ],
    }
    with pytest.raises(ValueError, match="review"):
        validate_document(doc)
