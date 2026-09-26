# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical Phase-G persistence for formerly transitional data types."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint

from backend.database import Base


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version = Column(String(50), primary_key=True)
    status = Column(String(20), nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    report_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)


class RadarObservation(Base):
    __tablename__ = "radar_observations"
    __table_args__ = (
        UniqueConstraint("scout_result_id", "captured_at", "legacy_snapshot_id", name="uq_radar_observation_identity"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scout_result_id = Column(String(36), nullable=False, index=True)
    legacy_snapshot_id = Column(Integer, nullable=True, unique=True)
    job_id = Column(String(36), nullable=True, index=True)
    captured_at = Column(DateTime, nullable=False, index=True)
    views = Column(Integer, nullable=True)
    likes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    favorites = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RadarSourceRun(Base):
    __tablename__ = "radar_source_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    legacy_source_run_id = Column(Integer, nullable=True, unique=True)
    platform = Column(String(20), nullable=False, index=True)
    keyword = Column(Text, nullable=False)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, index=True)
    records_raw = Column(Integer, nullable=True)
    records_normalized = Column(Integer, nullable=True)
    records_new = Column(Integer, nullable=True)
    records_updated = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    legacy_review_id = Column(Integer, nullable=True, unique=True)
    entity_type = Column(String(30), nullable=True, index=True)
    entity_id = Column(String(36), nullable=True, index=True)
    source_platform = Column(String(20), nullable=False, index=True)
    source_content_id = Column(String(200), nullable=False, index=True)
    stage = Column(String(30), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)
    note = Column(Text, nullable=True)
    workflow_source = Column(String(50), nullable=False, default="legacy_overlay")
    mapping_status = Column(String(30), nullable=False, default="mapped", index=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ContentAction(Base):
    __tablename__ = "content_actions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    legacy_action_id = Column(Integer, nullable=True, unique=True)
    source_platform = Column(String(20), nullable=False, index=True)
    source_content_id = Column(String(200), nullable=False, index=True)
    scout_result_id = Column(String(36), nullable=True, index=True)
    generated_video_id = Column(String(36), nullable=True, index=True)
    canonical_job_id = Column(String(36), nullable=True, index=True)
    legacy_job_id = Column(String(100), nullable=True)
    action = Column(String(50), nullable=False, index=True)
    params_json = Column(Text, nullable=True)
    mapping_status = Column(String(30), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PublicationRecord(Base):
    __tablename__ = "publication_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    legacy_publication_id = Column(Integer, nullable=True, unique=True)
    generated_video_id = Column(String(36), nullable=True, index=True)
    source_platform = Column(String(20), nullable=True, index=True)
    source_content_id = Column(String(200), nullable=True, index=True)
    platform = Column(String(20), nullable=False, index=True)
    job_id = Column(String(36), nullable=True, index=True)
    status = Column(String(30), nullable=False, index=True)
    title = Column(Text, nullable=True)
    caption = Column(Text, nullable=True)
    privacy = Column(String(50), nullable=True)
    provider_transaction_id = Column(String(200), nullable=True)
    public_content_id = Column(String(200), nullable=True)
    identifier_kind = Column(String(50), nullable=True)
    external_url = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    mapping_status = Column(String(30), nullable=False, default="mapped", index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)
