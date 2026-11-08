from __future__ import annotations

import asyncio
import json
import os
import socket
import sys

import uvicorn
from ai4music_core.config import Settings

from ai4music_engine.app import create_app


async def serve() -> None:
    token = os.environ["AI4MUSIC_LAUNCH_TOKEN"]
    settings = Settings.for_user(launch_token=token)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = int(sock.getsockname()[1])

    print(json.dumps({"event": "engine_bound", "port": port}), flush=True)

    config = uvicorn.Config(
        create_app(settings),
        log_config=None,
        access_log=False,
    )
    await uvicorn.Server(config).serve(sockets=[sock])


def main() -> None:
    if sys.argv[1:2] == ["--analysis-check"]:
        from pathlib import Path

        from ai4music_core.analysis.features import analyze_waveform, estimate_beats
        from ai4music_core.jobs.analysis_processing import write_json
        from ai4music_core.media.hashing import CancellationToken

        if len(sys.argv) != 4:
            raise SystemExit("Usage: --analysis-check normalized.wav new-output-directory")
        source, output = Path(sys.argv[2]), Path(sys.argv[3])
        output.mkdir(parents=True, exist_ok=False)
        token = CancellationToken()
        waveform = analyze_waveform(source, cancellation=token)
        beats = estimate_beats(source, cancellation=token)
        write_json(output / "waveform.json", waveform, token)
        write_json(output / "beats.json", beats, token)
        print(
            json.dumps(
                {
                    "event": "analysis_checked",
                    "durationSeconds": waveform["durationSeconds"],
                    "points": len(waveform["peaks"]),
                    "bpm": beats["bpm"],
                    "beatCount": len(beats["beats"]),
                }
            ),
            flush=True,
        )
        return
    if sys.argv[1:2] == ["--separation-worker"]:
        from ai4music_core.separation.worker import main as worker_main

        raise SystemExit(worker_main(sys.argv[2:]))
    if sys.argv[1:2] == ["--runtime-check"]:
        # Explicit release diagnostic; no model download or API/token is needed.
        import importlib.metadata

        import scipy
        import soundfile
        import torch
        import torchaudio
        from ai4music_core.media.ffmpeg import FFmpegTools
        from demucs.htdemucs import HTDemucs

        # The pinned checkpoint contains this older NumPy pickle namespace.
        importlib.import_module("numpy.core.multiarray")
        tools = FFmpegTools.discover()
        print(
            json.dumps(
                {
                    "event": "engine_runtime",
                    "torch_version": torch.__version__,
                    "torchaudio_version": torchaudio.__version__,
                    "soundfile_version": soundfile.__version__,
                    "demucs_version": importlib.metadata.version("demucs"),
                    "scipy_version": scipy.__version__,
                    "model_class": HTDemucs.__name__,
                    "cuda_available": torch.cuda.is_available(),
                    "ffmpeg": str(tools.ffmpeg_path),
                    "ffprobe": str(tools.ffprobe_path),
                }
            ),
            flush=True,
        )
        return
    asyncio.run(serve())


if __name__ == "__main__":
    main()
