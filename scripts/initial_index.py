import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.models import init_db
from app.db.session import SessionLocal, engine
from app.services.indexer import IndexService


if __name__ == "__main__":
    init_db(engine)
    with SessionLocal() as db:
        print(IndexService(db, get_settings()).scan())
