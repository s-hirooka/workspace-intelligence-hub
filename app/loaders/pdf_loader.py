from pathlib import Path

from pypdf import PdfReader

from app.domain import ExtractedChunk


def load_pdf(path: Path) -> list[ExtractedChunk]:
    reader = PdfReader(path)
    return [
        ExtractedChunk(text=text, chunk_type="pdf_page", page_number=number)
        for number, page in enumerate(reader.pages, start=1)
        if (text := (page.extract_text() or "").strip())
    ]
