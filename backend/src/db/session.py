"""Database session factories for both sync and async usage.

Sync sessions (psycopg2) are used by the orchestrator in background threads.
Async sessions (asyncpg) are used by FastAPI route handlers.
"""

import logging

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

# Module-level singletons (initialized via init_engines)
_async_engine = None
_sync_engine = None
_async_session_factory = None
_sync_session_factory = None


def _make_async_url(url: str) -> str:
    """Convert postgresql:// to postgresql+asyncpg://"""
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _ensure_tables(engine) -> None:
    """Create all tables if they don't already exist (idempotent)."""
    from db.models import Base
    try:
        Base.metadata.create_all(engine)
        logger.info("Database tables verified/created")
    except Exception as e:
        logger.warning(f"Could not auto-create tables: {e}")


def init_engines(db_config) -> bool:
    """Initialize both sync and async engines from DatabaseConfig.

    Returns True if engines were created, False on failure.
    """
    global _async_engine, _sync_engine, _async_session_factory, _sync_session_factory

    try:
        _async_engine = create_async_engine(
            _make_async_url(db_config.url),
            pool_size=db_config.pool_size,
            max_overflow=db_config.max_overflow,
            echo=False,
        )
        _async_session_factory = async_sessionmaker(
            _async_engine, expire_on_commit=False,
        )

        _sync_engine = create_engine(
            db_config.url,
            pool_size=db_config.pool_size,
            max_overflow=db_config.max_overflow,
            echo=False,
        )
        _sync_session_factory = sessionmaker(
            _sync_engine, expire_on_commit=False,
        )

        # Auto-create tables on startup (idempotent — no-op when they exist)
        _ensure_tables(_sync_engine)

        return True
    except Exception as e:
        logger.warning(f"Could not initialize database engines: {e}")
        return False


def get_async_engine():
    return _async_engine


def get_sync_engine():
    return _sync_engine


def get_async_session() -> AsyncSession:
    """Create a new async session (for FastAPI routes)."""
    if _async_session_factory is None:
        return None
    return _async_session_factory()


def get_sync_session() -> Session:
    """Create a new sync session (for orchestrator threads)."""
    if _sync_session_factory is None:
        return None
    return _sync_session_factory()


async def async_session_dep():
    """FastAPI dependency that yields an async session and auto-closes."""
    if _async_session_factory is None:
        yield None
        return
    async with _async_session_factory() as session:
        yield session
