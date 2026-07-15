"""add photo face columns and people table

Immich's REST API now exposes detected faces and recognized people
(``GET /faces``, ``GET /people``), which weren't available when `sync` was
built at steps 4-5. `sync` fetches faces per asset to populate ``face_count`` /
``person_ids`` on photos and to cache the album's recognized people, giving
`select` a coverage signal and `caption` name grounding — with no local
face-detection dependency. See knowledge/immich-enrichment.md.

Revision ID: c3d9e5a1f742
Revises: b7f2c1a4e8d0
Create Date: 2026-07-15 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d9e5a1f742"
down_revision: str | Sequence[str] | None = "b7f2c1a4e8d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both nullable: NULL means `sync` hasn't fetched faces for this photo yet
    # (the resumability invariant), distinct from a fetched-but-faceless photo
    # (face_count=0, person_ids=[]).
    op.add_column("photos", sa.Column("face_count", sa.Integer(), nullable=True))
    op.add_column("photos", sa.Column("person_ids", sa.JSON(), nullable=True))

    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("album_id", sa.Integer(), nullable=False),
        sa.Column("immich_person_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("is_lead", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["album_id"], ["albums.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("album_id", "immich_person_id"),
    )
    op.create_index(op.f("ix_people_album_id"), "people", ["album_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_people_album_id"), table_name="people")
    op.drop_table("people")
    op.drop_column("photos", "person_ids")
    op.drop_column("photos", "face_count")
