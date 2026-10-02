from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ExtractedChunk:
    text: str
    chunk_type: str = "text"
    language: str | None = None
    symbol_name: str | None = None
    page_number: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None


@dataclass(slots=True)
class ScannedFile:
    path: str
    project: str
    relative_path: str
    file_name: str
    extension: str
    content_hash: str
    modified_at: datetime
    source_table: str | None = None
    source_type: str = "unknown"
    source_id: str = "legacy"
    workspace_key: str = "workspace"
