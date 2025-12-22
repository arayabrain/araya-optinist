from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy.orm import sessionmaker
from sqlmodel import create_engine

from studio.app.common.db.config import DATABASE_CONFIG


def get_new_engine():
    return create_engine(
        DATABASE_CONFIG.DATABASE_URL,
        pool_recycle=360,
        pool_size=DATABASE_CONFIG.POOL_SIZE,
        max_overflow=20,  # Allow up to 20 additional connections beyond pool_size
        pool_pre_ping=True,  # Verify connections before using them
        pool_timeout=30,  # Wait up to 30 seconds for a connection
        echo_pool=False,  # Set to True for debugging connection pool
    )


@lru_cache
def get_cached_engine():
    return get_new_engine()


def get_session(use_cache: bool = True):
    engine = get_cached_engine() if use_cache else get_new_engine()
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
    )
    return SessionLocal()


@contextmanager
def session_scope(use_cache: bool = True):
    session = get_session(use_cache)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db(use_cache: bool = True):
    try:
        db = get_session(use_cache)
        yield db
    finally:
        db.close()
