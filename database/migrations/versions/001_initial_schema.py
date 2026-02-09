"""Initial schema - projects, iterations, providers tables.

Revision ID: 001
Revises: None
Create Date: 2026-02-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="idle"),
        sa.Column("current_phase", sa.String(50), nullable=False, server_default="idle"),
        sa.Column("current_iteration", sa.Integer, server_default="0"),
        sa.Column("max_iterations", sa.Integer, server_default="10"),
        sa.Column("quality_threshold", sa.Float, server_default="8.0"),
        sa.Column("last_score", sa.Float, nullable=True),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("total_cost", sa.Float, server_default="0.0"),
        sa.Column("total_tokens", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("last_update", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_created_at", "projects", ["created_at"])

    op.create_table(
        "iterations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("iteration_number", sa.Integer, nullable=False),
        sa.Column("agent", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer, server_default="0"),
        sa.Column("output_tokens", sa.Integer, server_default="0"),
        sa.Column("cost", sa.Float, server_default="0.0"),
        sa.Column("provider_name", sa.String(100), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("timestamp", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_iterations_project_id", "iterations", ["project_id"])
    op.create_index("ix_iterations_timestamp", "iterations", ["timestamp"])
    op.create_index("ix_iterations_agent", "iterations", ["agent"])

    op.create_table(
        "providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("config", JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("providers")
    op.drop_table("iterations")
    op.drop_table("projects")
