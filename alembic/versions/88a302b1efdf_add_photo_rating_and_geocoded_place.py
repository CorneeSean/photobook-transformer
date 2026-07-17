"""add photo rating and geocoded place columns

Immich exposes a manual star ``rating`` (−1…5) per asset and a reverse-geocoded
place (``city``/``state``/``country`` on ``exifInfo``) — data `sync` already
fetches, so no extra API call. `select` uses ``rating`` as a soft curation signal
and `caption` grounds on the place; both are opt-in and inert when absent
(genericity guard). OCR (the third, lower-value enrichment signal) is deliberately
deferred — see knowledge/build/enrichment.md step 20.

Revision ID: 88a302b1efdf
Revises: d4a1c7b93e56
Create Date: 2026-07-17 13:28:31.594188

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "88a302b1efdf"
down_revision: str | Sequence[str] | None = "d4a1c7b93e56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # All nullable: NULL means `sync` hasn't fetched this photo yet, or the photo
    # simply has no rating / no geocoded place — the resumability + genericity
    # invariant, same as the face columns.
    op.add_column("photos", sa.Column("rating", sa.Integer(), nullable=True))
    op.add_column("photos", sa.Column("city", sa.String(), nullable=True))
    op.add_column("photos", sa.Column("state", sa.String(), nullable=True))
    op.add_column("photos", sa.Column("country", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("photos", "country")
    op.drop_column("photos", "state")
    op.drop_column("photos", "city")
    op.drop_column("photos", "rating")
