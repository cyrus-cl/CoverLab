"""SQLite engine and session helpers for the local desktop application."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker


def configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_database_engine(database_url: str, *, echo: bool = False) -> Engine:
    engine = create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
    if engine.dialect.name != "sqlite":
        engine.dispose()
        raise ValueError("AI4Music local storage requires SQLite")
    event.listen(engine, "connect", configure_sqlite)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        # Lock before reading state, so completion cannot overwrite an
        # acknowledged cancellation read by a concurrent transaction.
        session.execute(text("BEGIN IMMEDIATE"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
