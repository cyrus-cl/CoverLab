from __future__ import annotations

from typing import Any

import pytest
from ai4music_core.config import Settings
from ai4music_engine import launcher


class FakeSocket:
    def setsockopt(self, *_args: object) -> None:
        pass

    def bind(self, address: tuple[str, int]) -> None:
        assert address == ("127.0.0.1", 0)

    def listen(self, backlog: int) -> None:
        assert backlog == 128

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 43123)


@pytest.mark.asyncio
async def test_launcher_announces_port_without_exposing_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    settings: Settings,
) -> None:
    token = "s" * 43
    fake_socket = FakeSocket()
    served_sockets: list[object] = []

    monkeypatch.setenv("AI4MUSIC_LAUNCH_TOKEN", token)
    monkeypatch.setattr(
        launcher.Settings,
        "for_user",
        classmethod(lambda _cls, *, launch_token: settings),
    )
    monkeypatch.setattr(launcher.socket, "socket", lambda *_args: fake_socket)
    monkeypatch.setattr(launcher.uvicorn, "Config", lambda *_args, **_kwargs: object())

    class FakeServer:
        def __init__(self, _config: object) -> None:
            pass

        async def serve(self, *, sockets: list[Any]) -> None:
            served_sockets.extend(sockets)

    monkeypatch.setattr(launcher.uvicorn, "Server", FakeServer)

    await launcher.serve()

    output = capsys.readouterr().out.strip()
    assert output == '{"event": "engine_bound", "port": 43123}'
    assert token not in output
    assert served_sockets == [fake_socket]
