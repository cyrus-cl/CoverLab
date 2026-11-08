"""Serialize stored UTC times consistently across SQLite and other databases."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_isoformat(value: datetime) -> str:
    # SQLite drops tzinfo from DateTime(timezone=True); stored values are UTC.
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()
