from pathlib import Path

from app.config import Settings
from app.domain import ExtractedChunk, ScannedFile
from app.loaders.csv_loader import load_csv
from app.loaders.docx_loader import load_docx
from app.loaders.excel_loader import load_excel
from app.loaders.pdf_loader import load_pdf
from app.loaders.text_loader import load_text
from app.loaders.sqlite_loader import load_sqlite_table


def load_file(path: Path, settings: Settings) -> list[ExtractedChunk]:
    extension = path.suffix.lower()
    if extension == ".pdf":
        return load_pdf(path)
    if extension == ".docx":
        return load_docx(path)
    if extension == ".xlsx":
        return load_excel(path, settings.excel_rows_per_chunk)
    if extension == ".csv":
        return load_csv(path, settings.csv_rows_per_chunk)
    return load_text(path)


def load_source(item: ScannedFile, settings: Settings) -> list[ExtractedChunk]:
    if item.source_table:
        if not settings.sqlite_database_path:
            raise ValueError("SQLite database path is not configured")
        return load_sqlite_table(settings.sqlite_database_path, item.source_table)
    return load_file(Path(item.path), settings)
