"""drop album lead_reference_asset_id

Lead-face resolution (stages/lead_faces.py) no longer caches uploaded reference
asset ids in the DB — it re-uploads the ``lead_faces.reference_images`` photos
every run and relies on Immich's content-dedup (identical bytes return the
existing asset id, a changed photo returns a new one). That makes swapping a lead
via ``--lead-face`` take effect with no stale-cache to invalidate, so the
``lead_reference_asset_id`` column added in ``d4a1c7b93e56`` (enrichment step 17b)
is now dead. Dropping a cache column needs no backfill.

Revision ID: a7e3f9c21b84
Revises: 88a302b1efdf
Create Date: 2026-07-18 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7e3f9c21b84"
down_revision: str | Sequence[str] | None = "88a302b1efdf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("albums", "lead_reference_asset_id")


def downgrade() -> None:
    op.add_column("albums", sa.Column("lead_reference_asset_id", sa.String(), nullable=True))
