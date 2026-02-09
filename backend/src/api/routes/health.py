"""Health check endpoint."""

from fastapi import APIRouter
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    db_status = "unavailable"
    try:
        from db.session import get_async_engine
        engine = get_async_engine()
        if engine:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            db_status = "connected"
    except Exception:
        pass

    return {"status": "ok", "version": "0.1.0", "database": db_status}
