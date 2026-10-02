from pathlib import Path

from docx import Document

from app.domain import ExtractedChunk


def load_docx(path: Path) -> list[ExtractedChunk]:
    document = Document(path)
    chunks: list[ExtractedChunk] = []
    heading = None
    buffer: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if paragraph.style and paragraph.style.name.lower().startswith("heading"):
            if buffer:
                chunks.append(ExtractedChunk(text="\n".join(buffer), chunk_type="docx_section", symbol_name=heading))
            heading, buffer = text, [text]
        else:
            buffer.append(text)
    if buffer:
        chunks.append(ExtractedChunk(text="\n".join(buffer), chunk_type="docx_section", symbol_name=heading))
    return chunks
