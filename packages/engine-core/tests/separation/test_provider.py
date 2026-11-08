from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from threading import Timer

import pytest
from ai4music_core.media.hashing import CancellationToken, JobCanceled


def test_provider_import_and_capabilities_do_not_import_torch(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys,json; from pathlib import Path; "
                "from ai4music_core.separation import DemucsProvider; "
                f"info=DemucsProvider(Path({str(tmp_path)!r})).runtime_info(); "
                "assert 'torch' not in sys.modules; print(json.dumps(info))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["model_id"] == "htdemucs"


def test_worker_command_dispatches_both_python_and_frozen(tmp_path: Path) -> None:
    from ai4music_core.separation.provider import worker_command

    assert worker_command(["--hello"], frozen=False) == [
        sys.executable,
        "-m",
        "ai4music_core.separation.worker",
        "--hello",
    ]
    assert worker_command(["--hello"], frozen=True) == [
        sys.executable,
        "--separation-worker",
        "--hello",
    ]


def test_cancellation_terminates_a_live_isolated_worker(tmp_path: Path) -> None:
    from ai4music_core.separation.provider import run_worker

    worker = tmp_path / "sleep.py"
    worker.write_text(
        'import time\nprint(\'{"event":"phase","phase":"infer"}\', flush=True)\ntime.sleep(60)'
    )
    token = CancellationToken()
    timer = Timer(0.5, token.request)
    timer.start()
    try:
        with pytest.raises(JobCanceled):
            run_worker([sys.executable, str(worker)], cancellation=token)
    finally:
        timer.cancel()


def test_worker_errors_are_actionable_without_dumping_raw_stderr(tmp_path: Path) -> None:
    from ai4music_core.separation.provider import SeparationError, run_worker

    worker = tmp_path / "fail.py"
    worker.write_text(
        "import sys\nprint('sensitive implementation detail', file=sys.stderr)\nsys.exit(3)"
    )
    with pytest.raises(SeparationError, match="worker_failed") as captured:
        run_worker([sys.executable, str(worker)], cancellation=CancellationToken())
    assert "sensitive" not in str(captured.value)


def test_frozen_runtime_does_not_claim_missing_modules_are_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai4music_core.separation import DemucsProvider

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
    assert DemucsProvider(tmp_path).runtime_info()["available"] is False
