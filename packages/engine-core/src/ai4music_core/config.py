from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_path
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI4MUSIC_", extra="forbid")

    launch_token: SecretStr = Field(min_length=43)
    data_dir: Path
    database_path: Path
    model_dir: Path
    cache_dir: Path
    log_dir: Path
    allowed_origins: tuple[str, ...] = (
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://127.0.0.1:1420",
    )

    @classmethod
    def for_user(cls, *, launch_token: str) -> Settings:
        data_dir = user_data_path("AI4Music", ensure_exists=True)
        return cls(
            launch_token=SecretStr(launch_token),
            data_dir=data_dir,
            database_path=data_dir / "app.db",
            model_dir=data_dir / "models",
            cache_dir=data_dir / "cache",
            log_dir=data_dir / "logs",
        )

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.model_dir,
            self.cache_dir,
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
