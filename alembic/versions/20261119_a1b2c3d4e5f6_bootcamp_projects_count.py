"""bootcamp projects_count

Revision ID: a1b2c3d4e5f6
Revises: 129ccaf28605
Create Date: 2026-11-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "129ccaf28605"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bootcamps", sa.Column("projects_count", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("bootcamps", "projects_count")
