from datetime import datetime

from pydantic import BaseModel, Field
from typing import Literal


class RagQuery(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    workspace_key: str = Field(default="workspace", pattern=r"^[a-z0-9][a-z0-9_-]*$")
    project: str = "all"
    top_k: int = Field(default=8, ge=1, le=30)
    mode: Literal["investigation", "proposal"] = "investigation"


class SourceItem(BaseModel):
    workspace_key: str = "workspace"
    project: str
    file_path: str
    relative_path: str
    symbol_name: str | None = None
    page_number: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    score: float
    source_type: str | None = None


class RagResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
