"""add album assembled_pdf_path

Revision ID: 9e650f717355
Revises: 0e2229f9f5f2
Create Date: 2026-07-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e650f717355"
down_revision: str | Sequence[str] | None = "0e2229f9f5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Path to the `assemble` stage's rendered PDF (e.g.
    # "data/albums/<slug>/assembled.pdf"). Integration contract with
    # `fulfill`, which reads this column to submit the print order.
    op.add_column("albums", sa.Column("assembled_pdf_path", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("albums", "assembled_pdf_path")
