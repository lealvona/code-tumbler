"""Analytics endpoints - cost timeseries and global stats from DB."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import async_session_dep
from db.repository import ProjectRepository

router = APIRouter(tags=["analytics"])
logger = logging.getLogger(__name__)

_EMPTY_STATS = {"project_count": 0, "total_cost": 0.0, "total_tokens": 0, "database": "unavailable"}


@router.get("/analytics/stats")
async def get_global_stats(session: AsyncSession = Depends(async_session_dep)):
    """Get global statistics across all projects."""
    if session is None:
        return _EMPTY_STATS
    try:
        return await ProjectRepository.async_get_global_stats(session)
    except Exception as e:
        logger.warning(f"analytics/stats query failed: {e}")
        return _EMPTY_STATS


@router.get("/analytics/cost-timeseries")
async def get_cost_timeseries(
    project: Optional[str] = None,
    session: AsyncSession = Depends(async_session_dep),
):
    """Get cost over time data for charting. Optional project filter."""
    if session is None:
        return []
    try:
        return await ProjectRepository.async_get_cost_timeseries(session, project)
    except Exception as e:
        logger.warning(f"analytics/cost-timeseries query failed: {e}")
        return []


@router.get("/analytics/cost-by-provider")
async def get_cost_by_provider(session: AsyncSession = Depends(async_session_dep)):
    """Get cost breakdown grouped by provider."""
    if session is None:
        return []
    try:
        return await ProjectRepository.async_get_cost_by_provider(session)
    except Exception as e:
        logger.warning(f"analytics/cost-by-provider query failed: {e}")
        return []


@router.get("/analytics/cost-per-iteration")
async def get_cost_per_iteration(
    project: str,
    session: AsyncSession = Depends(async_session_dep),
):
    """Get cost per iteration for a specific project."""
    if session is None:
        return []
    try:
        return await ProjectRepository.async_get_cost_per_iteration(session, project)
    except Exception as e:
        logger.warning(f"analytics/cost-per-iteration query failed: {e}")
        return []
