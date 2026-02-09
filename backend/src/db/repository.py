"""Database repository - data access methods for projects and iterations."""

from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from .models import Project, Iteration


class ProjectRepository:
    """Data access for projects and iterations tables."""

    # --- Sync methods (for StateManager / orchestrator threads) ---

    @staticmethod
    def sync_upsert_project(session: Session, name: str, state: Dict[str, Any]) -> Project:
        """Insert or update a project from state dict."""
        project = session.query(Project).filter(Project.name == name).first()
        if not project:
            project = Project(
                name=name,
                slug=name.lower().replace(" ", "-"),
            )
            session.add(project)

        project.status = state.get("status", "idle")
        project.current_phase = state.get("current_phase", "idle")
        project.current_iteration = state.get("iteration", 0)
        project.max_iterations = state.get("max_iterations", 10)
        project.quality_threshold = state.get("quality_threshold", 8.0)
        project.last_score = state.get("last_score")
        project.provider = state.get("provider")
        project.model = state.get("model")
        project.error = state.get("error")
        project.last_update = datetime.utcnow()

        if state.get("status") == "completed" and not project.completed_at:
            project.completed_at = datetime.utcnow()

        session.commit()
        return project

    @staticmethod
    def sync_log_iteration(
        session: Session,
        project_name: str,
        iteration_number: int,
        agent: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        provider_name: str = None,
        model_name: str = None,
        result: dict = None,
        score: float = None,
    ) -> Optional[Iteration]:
        """Insert an iteration record and update project rollup totals."""
        project = session.query(Project).filter(Project.name == project_name).first()
        if not project:
            return None

        iteration = Iteration(
            project_id=project.id,
            iteration_number=iteration_number,
            agent=agent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            provider_name=provider_name,
            model_name=model_name,
            result=result,
            score=score,
        )
        session.add(iteration)

        project.total_cost += cost
        project.total_tokens += input_tokens + output_tokens

        session.commit()
        return iteration

    # --- Async methods (for FastAPI routes) ---

    @staticmethod
    async def async_list_projects(session: AsyncSession) -> List[Project]:
        """List all projects ordered by creation date."""
        result = await session.execute(
            select(Project).order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def async_get_project(session: AsyncSession, name: str) -> Optional[Project]:
        """Get a single project by name."""
        result = await session.execute(
            select(Project).where(Project.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def async_delete_project(session: AsyncSession, name: str) -> bool:
        """Delete a project by name. Returns True if found and deleted."""
        project = await ProjectRepository.async_get_project(session, name)
        if not project:
            return False
        await session.delete(project)
        await session.commit()
        return True

    @staticmethod
    async def async_get_project_usage(
        session: AsyncSession, project_name: str,
    ) -> Dict[str, Any]:
        """Get aggregated usage data for a project (matches existing UsageData shape)."""
        project = await ProjectRepository.async_get_project(session, project_name)
        if not project:
            return {"total_tokens": 0, "total_cost": 0.0, "by_agent": {}, "history": []}

        result = await session.execute(
            select(Iteration)
            .where(Iteration.project_id == project.id)
            .order_by(Iteration.timestamp.asc())
        )
        iterations = list(result.scalars().all())

        by_agent: Dict[str, Dict] = {}
        history = []

        for it in iterations:
            if it.agent not in by_agent:
                by_agent[it.agent] = {"tokens": 0, "cost": 0.0, "calls": 0}
            by_agent[it.agent]["tokens"] += it.input_tokens + it.output_tokens
            by_agent[it.agent]["cost"] += it.cost
            by_agent[it.agent]["calls"] += 1

            history.append({
                "timestamp": it.timestamp.isoformat() + "Z",
                "agent": it.agent,
                "input_tokens": it.input_tokens,
                "output_tokens": it.output_tokens,
                "cost": it.cost,
            })

        return {
            "total_tokens": project.total_tokens,
            "total_cost": project.total_cost,
            "by_agent": by_agent,
            "history": history,
        }

    @staticmethod
    async def async_get_cost_timeseries(
        session: AsyncSession, project_name: str = None,
    ) -> List[Dict[str, Any]]:
        """Get cost over time data for charting."""
        query = select(
            func.date_trunc("hour", Iteration.timestamp).label("hour"),
            func.sum(Iteration.cost).label("cost"),
            func.sum(Iteration.input_tokens + Iteration.output_tokens).label("tokens"),
        )

        if project_name:
            project = await ProjectRepository.async_get_project(session, project_name)
            if project:
                query = query.where(Iteration.project_id == project.id)

        query = query.group_by("hour").order_by("hour")
        result = await session.execute(query)

        return [
            {
                "hour": row.hour.isoformat() + "Z",
                "cost": float(row.cost),
                "tokens": int(row.tokens),
            }
            for row in result.all()
        ]

    @staticmethod
    async def async_get_global_stats(session: AsyncSession) -> Dict[str, Any]:
        """Get dashboard-level aggregate stats across all projects."""
        result = await session.execute(
            select(
                func.count(Project.id).label("project_count"),
                func.coalesce(func.sum(Project.total_cost), 0).label("total_cost"),
                func.coalesce(func.sum(Project.total_tokens), 0).label("total_tokens"),
            )
        )
        row = result.one()
        return {
            "project_count": row.project_count,
            "total_cost": float(row.total_cost),
            "total_tokens": int(row.total_tokens),
        }

    @staticmethod
    async def async_get_cost_by_provider(session: AsyncSession) -> List[Dict[str, Any]]:
        """Get cost breakdown grouped by provider."""
        result = await session.execute(
            select(
                func.coalesce(Iteration.provider_name, "unknown").label("provider"),
                func.sum(Iteration.cost).label("cost"),
                func.sum(Iteration.input_tokens + Iteration.output_tokens).label("tokens"),
                func.count(Iteration.id).label("calls"),
            )
            .group_by("provider")
            .order_by(func.sum(Iteration.cost).desc())
        )
        return [
            {
                "provider": row.provider,
                "cost": float(row.cost),
                "tokens": int(row.tokens),
                "calls": int(row.calls),
            }
            for row in result.all()
        ]

    @staticmethod
    async def async_get_cost_per_iteration(
        session: AsyncSession, project_name: str,
    ) -> List[Dict[str, Any]]:
        """Get cost per iteration number for a project (refinement cost curve)."""
        project = await ProjectRepository.async_get_project(session, project_name)
        if not project:
            return []

        result = await session.execute(
            select(
                Iteration.iteration_number,
                Iteration.agent,
                func.sum(Iteration.cost).label("cost"),
                func.sum(Iteration.input_tokens + Iteration.output_tokens).label("tokens"),
            )
            .where(Iteration.project_id == project.id)
            .group_by(Iteration.iteration_number, Iteration.agent)
            .order_by(Iteration.iteration_number)
        )
        return [
            {
                "iteration": row.iteration_number,
                "agent": row.agent,
                "cost": float(row.cost),
                "tokens": int(row.tokens),
            }
            for row in result.all()
        ]
