import csv
from pathlib import Path

from app.domain import ExtractedChunk


def load_csv(path: Path, rows_per_chunk: int) -> list[ExtractedChunk]:
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                rows = list(csv.reader(stream))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeError("unsupported CSV encoding")
    if not rows:
        return []
    header, data = rows[0], rows[1:]
    chunks = []
    for offset in range(0, max(1, len(data)), rows_per_chunk):
        group = data[offset:offset + rows_per_chunk]
        text = "\n".join([",".join(header)] + [",".join(row) for row in group])
        chunks.append(ExtractedChunk(text=text, chunk_type="csv_rows", cell_range=f"rows {offset + 2}-{offset + len(group) + 1}"))
    return chunks
