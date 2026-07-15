"""add album lead_reference_asset_id

Caches the Immich asset id of an uploaded ``lead_faces.reference_image`` so a
re-run of lead-face resolution (stages/lead_faces.py) reuses it rather than
re-uploading the reference photo. See knowledge/immich-enrichment.md step 17b.

Revision ID: d4a1c7b93e56
Revises: c3d9e5a1f742
Create Date: 2026-07-15 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a1c7b93e56"
down_revision: str | Sequence[str] | None = "c3d9e5a1f742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("albums", sa.Column("lead_reference_asset_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("albums", "lead_reference_asset_id")
