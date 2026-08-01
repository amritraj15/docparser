from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings


def _normalize_db_url(url: str) -> str:
    """
    Some providers (Heroku-style URLs, and occasionally copy-pasted connection strings)
    still use the legacy `postgres://` scheme. SQLAlchemy 1.4+ rejects it outright rather
    than silently accepting it, so a URL that worked with `psycopg2` directly can still
    fail here with a confusing dialect error. Normalize defensively rather than document
    it as a gotcha someone has to remember every time they set DATABASE_URL.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


database_url = _normalize_db_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    # Import models so they're registered on Base.metadata before create_all
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
