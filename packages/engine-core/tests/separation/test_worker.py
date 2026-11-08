from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_worker_rejects_unverified_pickle_before_importing_the_model(tmp_path: Path) -> None:
    model = tmp_path / "model.th"
    model.write_bytes(b"invalid checkpoint")
    source = tmp_path / "input.wav"
    source.write_bytes(b"placeholder")
    output = tmp_path / "output"
    output.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai4music_core.separation.worker",
            "--model",
            str(model),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert completed.returncode == 1
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert events[-1] == {"event": "error", "code": "model_checksum_mismatch"}
    assert not list(output.iterdir())
    assert "Traceback" not in completed.stderr
