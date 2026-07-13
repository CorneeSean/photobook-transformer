"""add album lulu_order_id

Revision ID: 6a4c1293d989
Revises: 9e650f717355
Create Date: 2026-07-14 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a4c1293d989"
down_revision: str | Sequence[str] | None = "9e650f717355"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("albums", sa.Column("lulu_order_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("albums", "lulu_order_id")
