from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.db.models import Chunk


def similarity_search(db: Session, embedding: list[float], project: str, top_k: int,
                      workspace_key: str = "workspace"):
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")
    statement = select(Chunk, distance).where(Chunk.workspace_key == workspace_key)
    if project != "all":
        statement = statement.where(Chunk.project == project)
    statement = statement.order_by(distance).limit(top_k)
    return [(chunk, max(0.0, 1.0 - float(score))) for chunk, score in db.execute(statement).all()]


def lexical_search(db: Session, embedding: list[float], project: str, terms: tuple[str, ...], per_term: int = 4,
                   workspace_key: str = "workspace"):
    """Find code identifiers and paths that an embedding search can overlook.

    Query each term separately so a common term cannot crowd out a rare exact
    identifier. Index policy remains authoritative: only existing chunks are read.
    """
    if not isinstance(db, Session) or not terms:
        return []
    distance = Chunk.embedding.cosine_distance(embedding).label("distance")
    found = {}
    for term in terms[:6]:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        statement = select(Chunk, distance).where(
            Chunk.workspace_key == workspace_key,
            or_(Chunk.file_name.ilike(pattern, escape="\\"),
                Chunk.symbol_name.ilike(pattern, escape="\\"),
                Chunk.chunk_text.ilike(pattern, escape="\\")),
            Chunk.source_type != "database_snapshot",
        )
        if project != "all":
            statement = statement.where(Chunk.project == project)
        # A matching file/class declaration is stronger evidence than a passing
        # mention of its name in a generated overview.
        exactness = case(
            (Chunk.file_name.ilike(pattern, escape="\\"), 0),
            (Chunk.symbol_name.ilike(pattern, escape="\\"), 1),
            else_=2,
        )
        statement = statement.order_by(exactness, distance).limit(per_term)
        for chunk, dist in db.execute(statement).all():
            score = max(0.0, 1.0 - float(dist))
            found[chunk.id] = (chunk, max(score, found.get(chunk.id, (None, -1))[1]))
    return list(found.values())
