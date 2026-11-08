from __future__ import annotations

from pathlib import Path

from ai4music_core import config
from ai4music_core.config import Settings

LAUNCH_TOKEN = "t" * 43


def test_settings_create_only_the_configured_directories(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(
        launch_token=LAUNCH_TOKEN,
        data_dir=data_dir,
        database_path=data_dir / "app.db",
        model_dir=data_dir / "models",
        cache_dir=data_dir / "cache",
        log_dir=data_dir / "logs",
    )

    settings.ensure_directories()

    assert settings.data_dir.is_dir()
    assert settings.model_dir.is_dir()
    assert settings.cache_dir.is_dir()
    assert settings.log_dir.is_dir()
    assert not settings.database_path.exists()


def test_for_user_derives_paths_from_platform_data_dir(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "user_data_path", lambda *_args, **_kwargs: tmp_path)

    settings = Settings.for_user(launch_token=LAUNCH_TOKEN)

    assert settings.data_dir == tmp_path
    assert settings.database_path == tmp_path / "app.db"
    assert settings.model_dir == tmp_path / "models"
    assert settings.cache_dir == tmp_path / "cache"
    assert settings.log_dir == tmp_path / "logs"
