from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai4music_core.db.models import Project
from ai4music_core.db.session import session_scope
from ai4music_core.media.files import SourceSnapshot


class ProjectRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        snapshot: SourceSnapshot,
        output_dir: Path,
        name: str,
    ) -> Project:
        with session_scope(self._session_factory) as session:
            project = Project(
                name=name,
                source_path=str(snapshot.path),
                source_size=snapshot.size,
                source_mtime_ns=snapshot.mtime_ns,
                output_dir=str(output_dir),
            )
            session.add(project)
            session.flush()
            return project

    def list(self) -> list[Project]:
        with self._session_factory() as session:
            statement = select(Project).order_by(
                Project.last_opened_at.desc(),
                Project.created_at.desc(),
            )
            return list(session.scalars(statement))

    def get(self, project_id: UUID) -> Project | None:
        with self._session_factory() as session:
            return session.get(Project, project_id)
