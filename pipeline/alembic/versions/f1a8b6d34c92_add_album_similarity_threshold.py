"""add album similarity_threshold

Revision ID: f1a8b6d34c92
Revises: a7e3f9c21b84
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a8b6d34c92"
down_revision: str | Sequence[str] | None = "a7e3f9c21b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Per-album near-duplicate cutoff for `select`. NULL ⇒ the stage default
    # (0.9); lower it for dense burst/session albums (see stages/select.py).
    op.add_column("albums", sa.Column("similarity_threshold", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("albums", "similarity_threshold")
