from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)


@event.listens_for(engine, "connect")
def register_vector(dbapi_connection, _connection_record):
    try:
        from pgvector.psycopg import register_vector
        register_vector(dbapi_connection)
    except Exception:
        # Extension/table initialization may be occurring on the first connection.
        pass


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
