"""rename album lulu_order_id to output_ref

The ``fulfill`` stage is no longer Lulu-specific: it delegates to a pluggable
:class:`~album.printers.base.Printer` (HTML webpage by default, Lulu print order
opt-in), each of which records a printer-agnostic reference to what it produced.
The column that stored the Lulu order id is generalized to ``output_ref``.

Revision ID: b7f2c1a4e8d0
Revises: 6a4c1293d989
Create Date: 2026-07-14 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f2c1a4e8d0"
down_revision: str | Sequence[str] | None = "6a4c1293d989"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("albums", "lulu_order_id", new_column_name="output_ref")


def downgrade() -> None:
    op.alter_column("albums", "output_ref", new_column_name="lulu_order_id")
