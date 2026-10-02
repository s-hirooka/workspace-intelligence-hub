from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


DEFAULT_EXTENSIONS = {
    ".cs", ".csproj", ".sln", ".php", ".js", ".ts", ".tsx", ".jsx",
    ".py", ".sql", ".json", ".xml", ".config", ".yaml", ".yml",
    ".css", ".scss", ".html", ".htm",
    ".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv",
}

MANDATORY_EXCLUDED_DIRS = {
    # Generated/dependency directories.
    "bin", "obj", "node_modules", ".git", "vendor", "packages", "dist",
    "build", "cache", "tmp", ".vs", ".idea",
    # WordPress core, media, generated data and backups. These directories are
    # never useful RAG sources and may contain private or very large files.
    "wp-admin", "wp-includes", "uploads", "ai1wm-backups",
    "upgrade", "upgrade-temp-backup", "languages",
    # Third-party packages are described in the generated inventory instead of
    # indexing their full implementation. Custom themes/plugins remain eligible.
    "advanced-custom-fields-pro", "akismet", "all-in-one-wp-migration",
    "contact-form-7", "custom-facebook-feed",
    "duracelltomi-google-tag-manager", "flamingo", "instagram-feed",
    "query-monitor", "search-regex", "show-current-template", "siteguard",
    "tcd-classic-editor", "wp-mail-smtp", "wp-members",
    "xserver-typesquare-webfonts", "zipaddr-jp",
    "import-users-from-csv",
    # Commercial theme implementations are large third-party codebases. Their
    # Workspace-specific behavior is captured in the generated specifications.
    "gravity_tcd111", "solaris_tcd088",
    # Financial documents are operational records, not system specifications.
    "請求書",
    "twentytwentyfive", "twentytwentyfour", "twentytwentythree",
    "twentytwentytwo",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    openai_api_key: str = Field(default="", repr=False)
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    database_url: str = "postgresql+psycopg://workspace:change-me@localhost:5432/workspace_hub"
    source_root: Path = Path("sample-data")
    sources_config: Path | None = None
    index_policy_config: Path | None = None
    chunk_size: int = 1400
    chunk_overlap: int = 180
    max_file_size_mb: int = 50
    excluded_dirs: Annotated[set[str], NoDecode] = MANDATORY_EXCLUDED_DIRS
    supported_extensions: Annotated[set[str], NoDecode] = DEFAULT_EXTENSIONS
    csv_rows_per_chunk: int = 100
    excel_rows_per_chunk: int = 50
    embedding_batch_size: int = 64
    sqlite_database_path: Path | None = None
    session_hours: int = 12
    google_client_id: str = Field(default="", validation_alias=AliasChoices("GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID"), repr=False)
    google_client_secret: str = Field(default="", validation_alias=AliasChoices("GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET"), repr=False)
    google_redirect_uri: str = Field(default="http://127.0.0.1:8000/auth/google/callback", validation_alias=AliasChoices("GOOGLE_REDIRECT_URI", "GOOGLE_OAUTH_REDIRECT_URI"))
    google_allowed_emails: Annotated[set[str], NoDecode] = set()
    google_allowed_domain: str = ""
    managed_upload_dir: Path = Path("generated_docs/managed_uploads")
    backup_dir: Path = Path("backups")
    max_upload_size_mb: int = 25
    image_root: Path = Path("sample-data/images")
    image_yunet_model: Path = Path("models/face_detection_yunet_2023mar.onnx")
    image_sface_model: Path = Path("models/face_recognition_sface_2021dec.onnx")
    image_similarity_threshold: float = 0.45
    image_thumbnail_max_px: int = 640
    auto_index_enabled: bool = False
    auto_index_interval_minutes: int = 1440
    openai_monthly_budget_usd: float = 5.0
    openai_embedding_input_usd_per_million: float = 0.02
    openai_chat_input_usd_per_million: float = 0.15
    openai_chat_output_usd_per_million: float = 0.60
    sns_auto_sync_enabled: bool = True
    sns_auto_sync_interval_minutes: int = 1440
    sns_max_posts_per_platform: int = 200
    sns_incremental_posts_per_platform: int = 50
    sns_openai_analysis_enabled: bool = False
    youtube_workspace_key: str = "workspace"
    youtube_client_id: str = Field(default="", repr=False)
    youtube_client_secret: str = Field(default="", repr=False)
    youtube_refresh_token: str = Field(default="", repr=False)
    youtube_channel_id: str = ""
    # Workspace-specific Meta credentials use META_<WORKSPACE>_* variables.
    # The legacy META_* fields remain as a backwards-compatible fallback.
    meta_workspaces: Annotated[set[str], NoDecode] = set()
    meta_workspace_key: str = "workspace"
    meta_app_id: str = ""
    meta_app_secret: str = Field(default="", repr=False)
    meta_page_id: str = ""
    meta_instagram_account_id: str = ""
    meta_access_token: str = Field(default="", repr=False)
    meta_graph_version: str = Field(default="v26.0", validation_alias=AliasChoices("META_GRAPH_VERSION", "META_GRAPH_API_VERSION"))
    meta_request_max_retries: int = 2
    ga4_workspace_key: str = "workspace"
    ga4_property_id: str = ""
    ga4_credentials_path: Path = Path("secrets/ga4-service-account.json")

    @field_validator("excluded_dirs", "supported_extensions", mode="before")
    @classmethod
    def split_csv_setting(cls, value):
        if isinstance(value, str):
            return {item.strip().lower() for item in value.split(",") if item.strip()}
        return value

    @field_validator("google_allowed_emails", "meta_workspaces", mode="before")
    @classmethod
    def normalize_csv_identifiers(cls, value):
        if isinstance(value, str):
            return {item.strip().lower() for item in value.split(",") if item.strip()}
        return value

    @field_validator("meta_workspaces", mode="after")
    @classmethod
    def validate_meta_workspaces(cls, value: set[str]) -> set[str]:
        for workspace_key in value:
            if not workspace_key or not all(char.isalnum() or char in "-_" for char in workspace_key):
                raise ValueError("Meta workspace keys must contain only letters, numbers, '-' or '_'")
        return value
    @field_validator("excluded_dirs", mode="after")
    @classmethod
    def enforce_mandatory_exclusions(cls, value: set[str]) -> set[str]:
        """Environment overrides may add exclusions but cannot remove safety ones."""
        return {item.lower() for item in value} | MANDATORY_EXCLUDED_DIRS

    @field_validator("source_root")
    @classmethod
    def normalize_source_root(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("meta_workspace_key", "youtube_workspace_key", "ga4_workspace_key")
    @classmethod
    def normalize_integration_workspace_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not all(char.isalnum() or char in "-_" for char in normalized):
            raise ValueError("Integration workspace keys must contain only letters, numbers, '-' or '_'")
        return normalized

    @field_validator("sqlite_database_path")
    @classmethod
    def normalize_sqlite_path(cls, value: Path | None) -> Path | None:
        return value.expanduser() if value else None

    @field_validator("managed_upload_dir", "backup_dir", "image_root", "image_yunet_model", "image_sface_model", "ga4_credentials_path")
    @classmethod
    def normalize_runtime_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

@lru_cache
def get_settings() -> Settings:
    return Settings()
