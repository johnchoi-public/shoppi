"""mark one service per shop as the intake "Diagnosis" option

Revision ID: 8a1f0c2d4e6b
Revises: 17c19040a372
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "8a1f0c2d4e6b"
down_revision = "17c19040a372"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("service_types", sa.Column("is_diagnosis", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("service_types", "is_diagnosis")
