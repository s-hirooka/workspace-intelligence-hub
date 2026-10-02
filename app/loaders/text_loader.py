from pathlib import Path

from app.domain import ExtractedChunk

LANGUAGES = {
    ".cs": "csharp", ".csproj": "xml", ".sln": "solution", ".php": "php",
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".py": "python", ".sql": "sql", ".json": "json", ".xml": "xml",
    ".config": "xml", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".txt": "text",
    ".css": "css", ".scss": "scss", ".html": "html", ".htm": "html",
}


def load_text(path: Path) -> list[ExtractedChunk]:
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ValueError("binary content detected")
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeError("unsupported text encoding")
    return [ExtractedChunk(text=text, language=LANGUAGES.get(path.suffix.lower()), chunk_type="code" if path.suffix.lower() not in {".md", ".txt"} else "text")]
