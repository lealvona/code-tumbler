"""Shared fixtures for backend API tests."""

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.app import create_app
from db.session import async_session_dep


async def _no_db_session():
    """Override DB session to return None (no database available)."""
    return None


@pytest.fixture
def app():
    """Create a test FastAPI application with DB session overridden."""
    application = create_app()
    application.dependency_overrides[async_session_dep] = _no_db_session
    return application


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
