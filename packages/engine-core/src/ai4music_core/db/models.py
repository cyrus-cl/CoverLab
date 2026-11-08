"""Focused SQLite models for local AI4Music projects and analysis jobs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai4music_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


def _enum_values(enum_class: type[Enum]) -> list[str]:
    return [str(item.value) for item in enum_class]


def enum_column(enum_class: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_class,
        name=name,
        values_callable=_enum_values,
        validate_strings=True,
        create_constraint=True,
        native_enum=False,
    )


class ProjectStatus(StrEnum):
    READY = "ready"
    ANALYZING = "analyzing"
    COMPLETE = "complete"
    ERROR = "error"
    ARCHIVED = "archived"


class AnalysisStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELING = "canceling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class StageStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class StageAttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    WORKER_LOST = "worker_lost"
    TIMED_OUT = "timed_out"


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("source_size >= 0", name="source_size_non_negative"),
        CheckConstraint("source_mtime_ns >= 0", name="source_mtime_non_negative"),
        Index("ix_projects_status", "status"),
        Index("ix_projects_last_opened_at", "last_opened_at"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    output_dir: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        enum_column(ProjectStatus, "project_status"),
        default=ProjectStatus.READY,
        nullable=False,
    )
    last_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    analyses: Mapped[list[AnalysisJob]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AnalysisJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        CheckConstraint("progress_percent >= 0", name="progress_non_negative"),
        CheckConstraint("progress_percent <= 100", name="progress_at_most_100"),
        Index("ix_analysis_jobs_project_id", "project_id"),
        Index("ix_analysis_jobs_status", "status"),
        Index(
            "uq_analysis_jobs_single_active",
            text("1"),
            unique=True,
            sqlite_where=text("status IN ('created', 'queued', 'running', 'canceling')"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[AnalysisStatus] = mapped_column(
        enum_column(AnalysisStatus, "analysis_status"),
        default=AnalysisStatus.CREATED,
        nullable=False,
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="analyses")
    stages: Mapped[list[JobStage]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="analysis")


class JobStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "job_stages"
    __table_args__ = (
        UniqueConstraint("analysis_id", "stage_name", name="uq_job_stages_analysis_stage"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("bytes_done >= 0", name="bytes_done_non_negative"),
        CheckConstraint(
            "bytes_total IS NULL OR bytes_total >= 0",
            name="bytes_total_non_negative",
        ),
        CheckConstraint("progress_percent >= 0", name="progress_non_negative"),
        CheckConstraint("progress_percent <= 100", name="progress_at_most_100"),
        Index("ix_job_stages_analysis_id", "analysis_id"),
        Index("ix_job_stages_status", "status"),
    )

    analysis_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[StageStatus] = mapped_column(
        enum_column(StageStatus, "stage_status"),
        default=StageStatus.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, default=2, nullable=False)
    bytes_done: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)

    analysis: Mapped[AnalysisJob] = relationship(back_populates="stages")
    attempts: Mapped[list[JobStageAttempt]] = relationship(
        back_populates="stage",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="JobStageAttempt.attempt_number",
    )
    events: Mapped[list[JobEvent]] = relationship(back_populates="stage")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="stage")


class JobStageAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_stage_attempts"
    __table_args__ = (
        UniqueConstraint(
            "stage_id",
            "attempt_number",
            name="uq_job_stage_attempts_stage_number",
        ),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        Index("ix_job_stage_attempts_stage_id", "stage_id"),
        Index("ix_job_stage_attempts_status", "status"),
    )

    stage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_stages.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[StageAttemptStatus] = mapped_column(
        enum_column(StageAttemptStatus, "stage_attempt_status"),
        default=StageAttemptStatus.RUNNING,
        nullable=False,
    )
    process_id: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    stage: Mapped[JobStage] = relationship(back_populates="attempts")


class JobEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("analysis_id", "sequence", name="uq_job_events_analysis_sequence"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        Index("ix_job_events_analysis_id", "analysis_id"),
        Index("ix_job_events_created_at", "created_at"),
    )

    analysis_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_stages.id", ondelete="CASCADE"),
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    analysis: Mapped[AnalysisJob] = relationship(back_populates="events")
    stage: Mapped[JobStage | None] = relationship(back_populates="events")


class Artifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("path", name="uq_artifacts_path"),
        CheckConstraint("byte_size >= 0", name="byte_size_non_negative"),
        Index("ix_artifacts_project_id", "project_id"),
        Index("ix_artifacts_analysis_id", "analysis_id"),
        Index("ix_artifacts_kind", "kind"),
    )

    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    analysis_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="SET NULL"),
    )
    stage_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_stages.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        default=dict,
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    project: Mapped[Project] = relationship(back_populates="artifacts")
    analysis: Mapped[AnalysisJob | None] = relationship(back_populates="artifacts")
    stage: Mapped[JobStage | None] = relationship(back_populates="artifacts")


class ModelRelease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_releases"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model_id",
            "revision",
            name="uq_model_releases_identity",
        ),
        Index("ix_model_releases_capability_enabled", "capability", "enabled"),
    )

    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(512), nullable=False)
    revision: Mapped[str] = mapped_column(String(255), nullable=False)
    files_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    code_license: Mapped[str] = mapped_column(String(255), nullable=False)
    weights_license: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    installed_path: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
