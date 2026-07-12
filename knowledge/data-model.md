---
type: Data Model
title: Data model (SQLAlchemy ORM)
description: The Album, Segment, and Photo ORM models, and the invariants around nullability, timezones, and pgvector.
tags: [data-model, sqlalchemy, postgres, pgvector, schema]
timestamp: 2026-07-12
---

# Data model (SQLAlchemy ORM)

The [Postgres datastore](/architecture.md) is the source of truth for everything
the pipeline computes. See [Album definition](/album-definition.md) for how these
rows are created from YAML.

# Schema

```python
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Album(Base):
    """A bounded set of photos to be bound into a book.

    Deliberately event-type agnostic: a trip, a wedding, a baby's first year,
    a festival. Nothing here knows what the occasion *is*.
    """

    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True)      # "honeymoon-2026"
    title: Mapped[str]                                   # "Our Honeymoon"
    immich_album_id: Mapped[str | None]

    # Default IANA timezone (e.g. "Europe/Podgorica") for interpreting any
    # segment or photo time that arrives without an explicit UTC offset. Used
    # only at load/sync time to normalize everything to UTC in the DB; no stage
    # reasons about it afterwards, because every stored datetime is already UTC.
    timezone: Mapped[str | None]

    # Free-form escape hatch for album-specific context that stages don't
    # reason about, but caption prompts and templates may interpolate.
    # e.g. {"occasion": "honeymoon", "names": ["Alex", "Sam"]}
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    photos: Mapped[list["Photo"]] = relationship(back_populates="album")
    segments: Mapped[list["Segment"]] = relationship(
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

    position: Mapped[int]                 # ordering within the album (1, 2, 3...)
    label: Mapped[str]                    # "Day 4" | "Ceremony" | "Month 3"
    place: Mapped[str | None]             # "Lisbon" | "St. Mary's" | None
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
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # UTC
    camera_model: Mapped[str | None]
    gps_lat: Mapped[float | None]
    gps_lon: Mapped[float | None]
    duplicate_group_id: Mapped[str | None]

    # --- populated by `quality` ---
    blur_score: Mapped[float | None]           # Laplacian variance
    aesthetic_score: Mapped[float | None]      # LAION predictor
    # pgvector column — indexed cosine similarity, used by `cluster` + `select`.
    # Only populated if Immich doesn't expose its own embeddings (see open Q1).
    clip_embedding: Mapped[list[float] | None] = mapped_column(Vector(768))

    # --- populated by `cluster` ---
    segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("segments.id"), index=True
    )                                          # NULL if album has no segments
    scene_id: Mapped[int | None]               # sub-segment cluster

    # --- populated by `select` ---
    selected: Mapped[bool] = mapped_column(default=False)

    # --- populated by `enhance` / `render` ---
    enhance_exposure_ev: Mapped[float | None]
    enhance_temp_shift_k: Mapped[float | None]
    enhance_shadows: Mapped[float | None]
    enhance_highlights: Mapped[float | None]
    xmp_written: Mapped[bool] = mapped_column(default=False)
    rendered_path: Mapped[str | None]

    # --- populated by `caption` ---
    caption: Mapped[str | None]

    # --- populated by `assemble` ---
    page_number: Mapped[int | None]

    album: Mapped[Album] = relationship(back_populates="photos")
    segment: Mapped["Segment | None"] = relationship()
```

## Notes

- **`Segment` is nullable on `Photo` by design.** `segment_id IS NULL` means
  either the album has no segments, or the photo fell outside all of them.
  Distinguish the two in application logic and surface the latter as a warning
  rather than dropping the photo.
- **`clip_embedding` is a `pgvector` column**, so similarity queries
  ([`select`](/stages/select.md)'s MMR, [`cluster`](/stages/cluster.md)'s visual
  refinement) run in the database with an index rather than as brute-force numpy
  over every pair. Add an HNSW or IVFFlat index on it once populated.
  > **Testing caveat:** `pgvector` doesn't exist in SQLite, so pure-logic unit
  > tests that run against in-memory SQLite must not depend on it. Keep
  > similarity search behind a small function in `db/` that tests can substitute
  > — the *MMR selection logic* (which is what matters) is then testable in
  > SQLite with a stub similarity function, while the real `pgvector` query is
  > covered by a separate, slower test against a Postgres test container.
  > `testcontainers-python` is the clean way to do that.
- **`Album.meta` is deliberately dumb.** No stage branches on its contents; it's
  interpolated into caption prompts and templates only. Anything a stage needs to
  *reason* about belongs in a real column or a `Segment`.
- **All datetimes are stored timezone-aware, in UTC** (`DateTime(timezone=True)`).
  Segment boundaries in `album.yml` carry an explicit UTC offset (or inherit
  `Album.timezone`); [`definition.py`](/album-definition.md) converts them to UTC
  on load, and [`sync`](/stages/sync.md) normalizes each photo's capture time to
  UTC too. [`cluster`](/stages/cluster.md) therefore compares photo time against
  segment bounds entirely in UTC, so segment-edge assignment is unambiguous even
  when an album spans zones. (The remaining open question is only *how* a photo's
  source zone is determined — see [Open questions](/open-questions.md) #3.)
- Columns are grouped by the stage that populates them — a photo's row fills up
  left-to-right through the pipeline. Nullability is *meaningful*: `NULL` means
  "that stage hasn't run for this photo yet," which is what makes resumability
  work.
- Use **alembic migrations from the start**, never `Base.metadata.create_all()`.
