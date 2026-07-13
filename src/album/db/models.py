from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Album(Base):
    """A bounded set of photos to be bound into a book.

    Deliberately event-type agnostic: a trip, a wedding, a baby's first year,
    a festival. Nothing here knows what the occasion *is*.
    """

    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True)  # "honeymoon-2026"
    title: Mapped[str]  # "Our Honeymoon"
    immich_album_id: Mapped[str | None] = mapped_column(default=None)

    # Default IANA timezone (e.g. "Europe/Podgorica") for interpreting any
    # segment or photo time that arrives without an explicit UTC offset. Used
    # only at load/sync time to normalize everything to UTC in the DB; no stage
    # reasons about it afterwards, because every stored datetime is already UTC.
    timezone: Mapped[str | None] = mapped_column(default=None)

    # Free-form escape hatch for album-specific context that stages don't
    # reason about, but caption prompts and templates may interpolate.
    # e.g. {"occasion": "honeymoon", "names": ["Alex", "Sam"]}
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # --- populated by `assemble` ---
    # Path to the rendered print-ready PDF (e.g. "data/albums/<slug>/assembled.pdf").
    # Integration contract with `fulfill`, which reads this to submit the print
    # order — the name and semantics are agreed across both stages; don't rename.
    assembled_pdf_path: Mapped[str | None] = mapped_column(default=None)

    photos: Mapped[list[Photo]] = relationship(back_populates="album")
    segments: Mapped[list[Segment]] = relationship(
        back_populates="album", order_by="Segment.position"
    )


class Segment(Base):
    """An OPTIONAL named, time-bounded period within an album.

    Generalizes an itinerary day / wedding phase / milestone month. The
    pipeline treats these structurally (ordered, time-bounded, optionally
    placed) and never interprets their meaning.

    An album with zero segments is valid — `cluster` falls back to pure
    time-gap scene detection.
    """

    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("album_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id"), index=True)

    position: Mapped[int]  # ordering within the album (1, 2, 3...)
    label: Mapped[str]  # "Day 4" | "Ceremony" | "Month 3"
    place: Mapped[str | None] = mapped_column(default=None)  # "Lisbon" | "St. Mary's" | None
    # Stored tz-aware in UTC. album.yml supplies these with an explicit offset
    # (or falls back to Album.timezone); definition.py normalizes to UTC on load.
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    album: Mapped[Album] = relationship(back_populates="segments")


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id"), index=True)

    # --- populated by `sync` (from Immich) ---
    immich_asset_id: Mapped[str] = mapped_column(unique=True)
    filename: Mapped[str]
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)  # UTC
    camera_model: Mapped[str | None] = mapped_column(default=None)
    gps_lat: Mapped[float | None] = mapped_column(default=None)
    gps_lon: Mapped[float | None] = mapped_column(default=None)
    duplicate_group_id: Mapped[str | None] = mapped_column(default=None)

    # --- populated by `quality` ---
    blur_score: Mapped[float | None] = mapped_column(default=None)  # Laplacian variance
    aesthetic_score: Mapped[float | None] = mapped_column(default=None)  # LAION predictor
    # pgvector column — indexed cosine similarity, used by `cluster` + `select`.
    # Only populated if Immich doesn't expose its own embeddings (see open Q1).
    clip_embedding: Mapped[list[float] | None] = mapped_column(Vector(768), default=None)

    # --- populated by `cluster` ---
    segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("segments.id"), index=True, default=None
    )  # NULL if album has no segments
    scene_id: Mapped[int | None] = mapped_column(default=None)  # sub-segment cluster

    # --- populated by `select` ---
    selected: Mapped[bool] = mapped_column(default=False)

    # --- populated by `enhance` / `render` ---
    enhance_exposure_ev: Mapped[float | None] = mapped_column(default=None)
    enhance_temp_shift_k: Mapped[float | None] = mapped_column(default=None)
    enhance_shadows: Mapped[float | None] = mapped_column(default=None)
    enhance_highlights: Mapped[float | None] = mapped_column(default=None)
    xmp_written: Mapped[bool] = mapped_column(default=False)
    rendered_path: Mapped[str | None] = mapped_column(default=None)

    # --- populated by `caption` ---
    caption: Mapped[str | None] = mapped_column(default=None)

    # --- populated by `assemble` ---
    page_number: Mapped[int | None] = mapped_column(default=None)

    album: Mapped[Album] = relationship(back_populates="photos")
    segment: Mapped[Segment | None] = relationship()
