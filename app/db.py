"""Sync SQLAlchemy engine/session. Deliberately not async — see the
"Why sync SQLAlchemy" note in SPEC.md. Route handlers that touch the DB are
declared as plain `def` (Starlette runs those in a threadpool); the async
discovery loop reaches the DB via asyncio.to_thread(...).
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def session_scope() -> Session:
    """For use outside request handlers (the sync loop, via asyncio.to_thread).
    Caller is responsible for closing/committing.
    """
    return SessionLocal()
