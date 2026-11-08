from ai4music_core.lyrics.bailian import timed_document


def test_cloud_timestamps_use_audio_seconds_and_keep_existing_schema():
    result = timed_document(
        {
            "transcripts": [
                {
                    "channel_id": 0,
                    "sentences": [
                        {
                            "begin_time": 200,
                            "end_time": 1200,
                            "text": "你好。",
                            "words": [
                                {"begin_time": 200, "end_time": 610, "text": "你"},
                                {"begin_time": 600, "end_time": 1200, "text": "好"},
                            ],
                        },
                        {"begin_time": 1180, "end_time": 2010, "text": "再见。"},
                    ],
                }
            ],
        },
        2.0,
    )
    first, second = result["segments"]
    assert first["start"] == 0.2 and first["end"] == 1.2
    assert first["text"] == "你好。"
    assert first["words"][1] == {"start": 0.61, "end": 1.2, "word": "好"}
    assert second["start"] == 1.2 and second["end"] == 2.0
