from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker
from sqlmodel import create_engine

from studio.app.common.db.config import DATABASE_CONFIG, get_ssl_creator


def get_new_engine():
    kwargs = {}
    creator = get_ssl_creator()
    if creator:
        kwargs["creator"] = creator
    return create_engine(
        DATABASE_CONFIG.DATABASE_URL,
        pool_recycle=360,
        pool_size=DATABASE_CONFIG.POOL_SIZE,
        max_overflow=DATABASE_CONFIG.MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_timeout=30,
        echo_pool=False,
        **kwargs,
    )


@lru_cache
def get_cached_engine():
    return get_new_engine()


def get_session(use_cache: bool = True):
    """
    Session on the process's cached engine, or on a new one.

    - use_cache=False builds an engine that the caller owns: closing the
      session does not dispose it, so call session.get_bind().dispose().
    - session_scope(use_cache=False) does not dispose it either.
    """
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


def get_db():
    """
    Request-scoped session on the cached engine, for Depends().

    Takes no parameters: FastAPI turns every parameter of a dependency into a
    request parameter of each operation that depends on it.
    """
    try:
        db = get_session()
        yield db
    finally:
        db.close()


def release_request_session(db: Session) -> None:
    """
    Return a get_db session's connection to the pool before background tasks run.

    - FastAPI 0.104 tears get_db down only after the request's background tasks
      finish, so a long task keeps the connection checked out until it is
      dropped on idle timeout, and teardown then fails on the dead connection.
    - Call it in a finally block of any endpoint that queues long background
      work. The session stays usable; the next query checks out a new
      connection.
    - It detaches every object loaded through the session. Loaded attributes
      stay readable, but a lazy load (e.g. a relationship) after the call
      raises DetachedInstanceError: read what is needed before releasing.
    - Background tasks never receive this session; they open their own
      session_scope().
    """
    db.close()
