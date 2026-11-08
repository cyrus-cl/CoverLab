from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from ai4music_core.config import Settings
from ai4music_engine.app import create_app
from fastapi.testclient import TestClient

LAUNCH_TOKEN = "t" * 43
ClientFactory = Callable[[], TestClient]


@pytest.fixture
def settings(tmp_path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        launch_token=LAUNCH_TOKEN,
        data_dir=data_dir,
        database_path=data_dir / "app.db",
        model_dir=data_dir / "models",
        cache_dir=data_dir / "cache",
        log_dir=data_dir / "logs",
    )


@pytest.fixture
def client_factory(settings: Settings) -> ClientFactory:
    def create_client() -> TestClient:
        return TestClient(
            create_app(settings),
            base_url="http://127.0.0.1",
            headers={"X-AI4Music-Token": LAUNCH_TOKEN},
        )

    return create_client


@pytest.fixture
def client(client_factory: ClientFactory) -> Iterator[TestClient]:
    with client_factory() as test_client:
        yield test_client


@pytest.fixture
def client_without_token(settings: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings),
        base_url="http://127.0.0.1",
    ) as test_client:
        yield test_client
