"""create albums, segments, photos

Revision ID: 0e2229f9f5f2
Revises:
Create Date: 2026-07-13 08:47:58.428992

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0e2229f9f5f2"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # clip_embedding (photos) is a pgvector column — the extension must exist
    # before that column type can be created.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "albums",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("immich_album_id", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("album_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("place", sa.String(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["album_id"], ["albums.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("album_id", "position"),
    )
    op.create_index(op.f("ix_segments_album_id"), "segments", ["album_id"], unique=False)
    op.create_table(
        "photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("album_id", sa.Integer(), nullable=False),
        sa.Column("immich_asset_id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("camera_model", sa.String(), nullable=True),
        sa.Column("gps_lat", sa.Float(), nullable=True),
        sa.Column("gps_lon", sa.Float(), nullable=True),
        sa.Column("duplicate_group_id", sa.String(), nullable=True),
        sa.Column("blur_score", sa.Float(), nullable=True),
        sa.Column("aesthetic_score", sa.Float(), nullable=True),
        sa.Column("clip_embedding", pgvector.sqlalchemy.Vector(768), nullable=True),
        sa.Column("segment_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("enhance_exposure_ev", sa.Float(), nullable=True),
        sa.Column("enhance_temp_shift_k", sa.Float(), nullable=True),
        sa.Column("enhance_shadows", sa.Float(), nullable=True),
        sa.Column("enhance_highlights", sa.Float(), nullable=True),
        sa.Column("xmp_written", sa.Boolean(), nullable=False),
        sa.Column("rendered_path", sa.String(), nullable=True),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["album_id"], ["albums.id"]),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("immich_asset_id"),
    )
    op.create_index(op.f("ix_photos_album_id"), "photos", ["album_id"], unique=False)
    op.create_index(op.f("ix_photos_segment_id"), "photos", ["segment_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_photos_segment_id"), table_name="photos")
    op.drop_index(op.f("ix_photos_album_id"), table_name="photos")
    op.drop_table("photos")
    op.drop_index(op.f("ix_segments_album_id"), table_name="segments")
    op.drop_table("segments")
    op.drop_table("albums")
    op.execute("DROP EXTENSION IF EXISTS vector")
