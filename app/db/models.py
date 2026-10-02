from datetime import date, datetime, timezone
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    industry: Mapped[str] = mapped_column(String(200), default="")
    business_summary: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IndexedFile(Base):
    __tablename__ = "indexed_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_key: Mapped[str] = mapped_column(String(100), default="workspace", server_default="workspace", index=True)
    project: Mapped[str] = mapped_column(String(255), index=True)
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    relative_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(20))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="indexed_file", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("indexed_file_id", "chunk_index", name="uq_file_chunk_index"),
        Index("ix_chunks_project", "project"),
        Index("ix_chunks_workspace_project", "workspace_key", "project"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    indexed_file_id: Mapped[str] = mapped_column(ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    workspace_key: Mapped[str] = mapped_column(String(100), default="workspace", server_default="workspace")
    project: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(Text)
    relative_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(20))
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    symbol_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(50), default="text")
    chunk_text: Mapped[str] = mapped_column(Text)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    cell_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), default="unknown", server_default="unknown")
    source_id: Mapped[str] = mapped_column(String(100), default="legacy", server_default="legacy")
    pii_detected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pii_types: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dimensions))
    indexed_file: Mapped[IndexedFile] = relationship(back_populates="chunks")


class IndexRun(Base):
    __tablename__ = "index_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    total: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    label: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    question_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class CodeSymbol(Base):
    __tablename__ = "code_symbols"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project: Mapped[str] = mapped_column(String(255), index=True)
    file_path: Mapped[str] = mapped_column(Text, index=True)
    language: Mapped[str] = mapped_column(String(30))
    symbol_type: Mapped[str] = mapped_column(String(40))
    symbol_name: Mapped[str] = mapped_column(Text)
    parent_symbol: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))


class ExternalResource(Base):
    __tablename__ = "external_resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project: Mapped[str] = mapped_column(String(255), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(String(30))
    resource_name: Mapped[str] = mapped_column(Text)
    endpoint_masked: Mapped[str | None] = mapped_column(Text)
    symbol_name: Mapped[str | None] = mapped_column(Text)


class SystemRelation(Base):
    __tablename__ = "system_relations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_project: Mapped[str] = mapped_column(String(255), index=True)
    target_project: Mapped[str] = mapped_column(String(255), index=True)
    relation_type: Mapped[str] = mapped_column(String(40))
    relation_key: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    source_file: Mapped[str] = mapped_column(Text)
    target_file: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)

class ImageAsset(Base):
    __tablename__ = "image_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    file_path: Mapped[str] = mapped_column(Text, unique=True, index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text, index=True)
    extension: Mapped[str] = mapped_column(String(20))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    face_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    smiling_face_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    analysis_status: Mapped[str] = mapped_column(String(30), default="completed", index=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    ai_tags: Mapped[list] = mapped_column(JSON, default=list)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_lines: Mapped[list] = mapped_column(JSON, default=list)
    title_candidates: Mapped[list] = mapped_column(JSON, default=list)
    content_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_analysis_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    content_analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    capture_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    capture_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capture_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capture_source: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    capture_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    capture_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    faces: Mapped[list["ImageFace"]] = relationship(back_populates="image", cascade="all, delete-orphan")


class ImageFace(Base):
    __tablename__ = "image_faces"
    __table_args__ = (UniqueConstraint("image_id", "face_index", name="uq_image_face_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    image_id: Mapped[str] = mapped_column(ForeignKey("image_assets.id", ondelete="CASCADE"), index=True)
    face_index: Mapped[int] = mapped_column(Integer)
    bbox: Mapped[list] = mapped_column(JSON, default=list)
    smiling: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    smile_score: Mapped[float] = mapped_column(Float, default=0.0)
    embedding: Mapped[list[float]] = mapped_column(Vector(128))
    person_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    person_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    image: Mapped[ImageAsset] = relationship(back_populates="faces")

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, unique=True)
    auth_provider: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    google_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthLoginState(Base):
    __tablename__ = "oauth_login_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(120))
    code_verifier: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

class OperationLog(Base):
    __tablename__ = "operation_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    client_ip: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    operation: Mapped[str] = mapped_column(String(40), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    request_count: Mapped[int] = mapped_column(Integer, default=1)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class BackupRecord(Base):
    __tablename__ = "backup_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_name: Mapped[str] = mapped_column(Text, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    note: Mapped[str] = mapped_column(Text, default="")


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class MetaConnection(Base):
    __tablename__ = "meta_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    facebook_page_id: Mapped[str] = mapped_column(String(255), default="")
    facebook_page_name: Mapped[str] = mapped_column(String(255), default="")
    instagram_account_id: Mapped[str] = mapped_column(String(255), default="")
    instagram_username: Mapped[str] = mapped_column(String(255), default="")
    instagram_name: Mapped[str] = mapped_column(String(255), default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connection_status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InstagramAccountSnapshot(Base):
    __tablename__ = "instagram_account_snapshots"
    __table_args__ = (
        UniqueConstraint("connection_id", "snapshot_date", name="uq_instagram_account_snapshot_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    connection_id: Mapped[str] = mapped_column(ForeignKey("meta_connections.id", ondelete="CASCADE"), index=True)
    instagram_account_id: Mapped[str] = mapped_column(String(255), index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    followers_count: Mapped[int] = mapped_column(BigInteger, default=0)
    media_count: Mapped[int] = mapped_column(BigInteger, default=0)
    profile_links_taps_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    accounts_engaged_this_month: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reach_this_month: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    follower_demographics: Mapped[dict] = mapped_column(JSON, default=dict)
    engaged_audience_demographics: Mapped[dict] = mapped_column(JSON, default=dict)
    reached_audience_demographics: Mapped[dict] = mapped_column(JSON, default=dict)
    account_insights_unavailable: Mapped[list] = mapped_column(JSON, default=list)
    account_insight_errors: Mapped[dict] = mapped_column(JSON, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SnsPost(Base):
    __tablename__ = "sns_posts"
    __table_args__ = (UniqueConstraint("workspace_key", "platform", "external_id", name="uq_sns_post_workspace_platform_external"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_key: Mapped[str] = mapped_column(String(100), default="workspace", server_default="workspace", index=True)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    account_name: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    permalink: Mapped[str] = mapped_column(Text, default="")
    media_type: Mapped[str] = mapped_column(String(50), default="")
    media_product_type: Mapped[str] = mapped_column(String(50), default="")
    thumbnail_url: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metrics: Mapped[list["SnsMetricSnapshot"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class SnsMetricSnapshot(Base):
    __tablename__ = "sns_metric_snapshots"
    __table_args__ = (UniqueConstraint("post_id", "measured_date", name="uq_sns_metric_post_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    post_id: Mapped[str] = mapped_column(ForeignKey("sns_posts.id", ondelete="CASCADE"), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    measured_date: Mapped[date] = mapped_column(Date, index=True)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    reach: Mapped[int] = mapped_column(BigInteger, default=0)
    views: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    profile_visits: Mapped[int] = mapped_column(BigInteger, default=0)
    link_clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    inquiries: Mapped[int] = mapped_column(BigInteger, default=0)
    reservations: Mapped[int] = mapped_column(BigInteger, default=0)
    likes: Mapped[int] = mapped_column(BigInteger, default=0)
    comments: Mapped[int] = mapped_column(BigInteger, default=0)
    shares: Mapped[int] = mapped_column(BigInteger, default=0)
    saves: Mapped[int] = mapped_column(BigInteger, default=0)
    watch_time_seconds: Mapped[int] = mapped_column(BigInteger, default=0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    raw_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    post: Mapped[SnsPost] = relationship(back_populates="metrics")


class InstagramMediaInsightSnapshot(Base):
    __tablename__ = "instagram_media_insight_snapshots"
    __table_args__ = (
        UniqueConstraint("post_id", "snapshot_date", name="uq_instagram_media_insight_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    post_id: Mapped[str] = mapped_column(ForeignKey("sns_posts.id", ondelete="CASCADE"), index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    reach: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    saved: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    profile_visits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    profile_activity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    follows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    unavailable_metrics: Mapped[list] = mapped_column(JSON, default=list)
    metric_errors: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SnsSyncRun(Base):
    __tablename__ = "sns_sync_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_key: Mapped[str] = mapped_column(String(100), default="workspace", server_default="workspace", index=True)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    profile_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    media_found: Mapped[int] = mapped_column(Integer, default=0)
    insights_synced: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default="")
Index(
    "ix_image_faces_embedding_hnsw", ImageFace.embedding,
    postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
)
Index(
    "ix_chunks_embedding_hnsw", Chunk.embedding,
    postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
)


def init_db(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        # The first pooled connection can predate CREATE EXTENSION, so register it explicitly.
        from pgvector.psycopg import register_vector
        register_vector(connection.connection.driver_connection)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE indexed_files ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace'")
        connection.exec_driver_sql("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace'")
        connection.exec_driver_sql("ALTER TABLE sns_posts ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace'")
        connection.exec_driver_sql("ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS workspace_key VARCHAR(100) NOT NULL DEFAULT 'workspace'")
        connection.exec_driver_sql("ALTER TABLE sns_posts ADD COLUMN IF NOT EXISTS media_product_type VARCHAR(50) NOT NULL DEFAULT ''")
        connection.exec_driver_sql("ALTER TABLE sns_posts ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        connection.exec_driver_sql("ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS profile_synced BOOLEAN NOT NULL DEFAULT false")
        connection.exec_driver_sql("ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS media_found INTEGER NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS insights_synced INTEGER NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE sns_sync_runs ADD COLUMN IF NOT EXISTS error_summary JSON NOT NULL DEFAULT '{}'::json")
        connection.exec_driver_sql("ALTER TABLE meta_connections ADD COLUMN IF NOT EXISTS instagram_name VARCHAR(255) NOT NULL DEFAULT ''")
        connection.exec_driver_sql("ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS profile_visits BIGINT NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS link_clicks BIGINT NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS inquiries BIGINT NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE sns_metric_snapshots ADD COLUMN IF NOT EXISTS reservations BIGINT NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE instagram_media_insight_snapshots ADD COLUMN IF NOT EXISTS profile_visits BIGINT")
        connection.exec_driver_sql("ALTER TABLE instagram_media_insight_snapshots ADD COLUMN IF NOT EXISTS profile_activity BIGINT")
        connection.exec_driver_sql("ALTER TABLE instagram_media_insight_snapshots ADD COLUMN IF NOT EXISTS follows BIGINT")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS profile_links_taps_30d BIGINT")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS accounts_engaged_this_month BIGINT")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS reach_this_month BIGINT")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS follower_demographics JSON NOT NULL DEFAULT '{}'::json")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS engaged_audience_demographics JSON NOT NULL DEFAULT '{}'::json")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS reached_audience_demographics JSON NOT NULL DEFAULT '{}'::json")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS account_insights_unavailable JSON NOT NULL DEFAULT '[]'::json")
        connection.exec_driver_sql("ALTER TABLE instagram_account_snapshots ADD COLUMN IF NOT EXISTS account_insight_errors JSON NOT NULL DEFAULT '{}'::json")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_indexed_files_workspace_key ON indexed_files(workspace_key)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_chunks_workspace_project ON chunks(workspace_key, project)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sns_posts_workspace_key ON sns_posts(workspace_key)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sns_sync_runs_workspace_key ON sns_sync_runs(workspace_key)")
        connection.exec_driver_sql("ALTER TABLE sns_posts DROP CONSTRAINT IF EXISTS uq_sns_post_platform_external")
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_sns_post_workspace_platform_external ON sns_posts(workspace_key, platform, external_id)")
        connection.exec_driver_sql(
            "INSERT INTO workspaces (key, name, industry, business_summary, goals, active, created_at, updated_at) "
            "VALUES ('workspace', '宅建', '不動産・宅建', '', '[]'::json, true, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        )
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(320)")
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) NOT NULL DEFAULT 'local'")
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_subject VARCHAR(255)")
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users(email) WHERE email IS NOT NULL")
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_subject ON users(google_subject) WHERE google_subject IS NOT NULL")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS ai_tags JSONB NOT NULL DEFAULT '[]'::jsonb")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS ocr_text TEXT NOT NULL DEFAULT ''")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS ocr_lines JSONB NOT NULL DEFAULT '[]'::jsonb")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS title_candidates JSONB NOT NULL DEFAULT '[]'::jsonb")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS content_analyzed_at TIMESTAMPTZ")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS content_analysis_status VARCHAR(30) NOT NULL DEFAULT 'pending'")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS content_analysis_error TEXT")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS capture_at TIMESTAMPTZ")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS capture_year INTEGER")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS capture_month INTEGER")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS capture_day INTEGER")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS capture_source VARCHAR(30)")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS capture_confidence VARCHAR(20)")
        connection.exec_driver_sql("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS capture_raw TEXT")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_image_assets_capture_at ON image_assets(capture_at)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_image_assets_capture_year ON image_assets(capture_year)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_image_assets_capture_source ON image_assets(capture_source)")
    # Keep existing Phase 1 databases compatible with the longer
    # "completed_with_errors" state name.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE index_runs ALTER COLUMN status TYPE VARCHAR(30)"
        )
