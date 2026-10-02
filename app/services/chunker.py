import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import Settings
from app.domain import ExtractedChunk
from app.services.secret_filter import sanitize_text

SYMBOL_PATTERNS = {
    "csharp": re.compile(r"(?m)^\s*(?:namespace|class|interface|record|enum)\s+([\w.]+)|^\s*(?:public|private|protected|internal|static|async|virtual|override|sealed|partial|\s)+[\w<>,?\[\].]+\s+(\w+)\s*\("),
    "php": re.compile(r"(?mi)^\s*(?:class|function)\s+(\w+)"),
    "javascript": re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+(\w+)|^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("),
    "typescript": re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+(\w+)|^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("),
    "python": re.compile(r"(?m)^\s*(?:async\s+)?(?:class|def)\s+(\w+)"),
    "sql": re.compile(r"(?mi)^\s*(?:CREATE\s+(?:TABLE|VIEW|PROCEDURE|FUNCTION)\s+|SELECT\s+)([\w.\[\]`\"]+)?"),
}


def detect_symbol(text: str, language: str | None) -> str | None:
    pattern = SYMBOL_PATTERNS.get(language or "")
    if not pattern:
        return None
    match = pattern.search(text)
    if not match:
        return None
    return next((group for group in match.groups() if group), match.group(0).strip()[:200])


def chunk_extracted(items: list[ExtractedChunk], path: Path, settings: Settings) -> list[ExtractedChunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\nclass ", "\nfunction ", "\ndef ", "\nCREATE ", "\n\n", "\n", " ", ""],
    )
    output: list[ExtractedChunk] = []
    for item in items:
        safe = sanitize_text(item.text, path.name)
        if safe is None:
            continue
        for text in splitter.split_text(safe):
            if not text.strip():
                continue
            output.append(ExtractedChunk(
                text=text, chunk_type=item.chunk_type, language=item.language,
                symbol_name=item.symbol_name or detect_symbol(text, item.language),
                page_number=item.page_number, sheet_name=item.sheet_name, cell_range=item.cell_range,
            ))
    return output
