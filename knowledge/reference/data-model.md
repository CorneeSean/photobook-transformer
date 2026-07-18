---
type: Data Model
title: Data model (SQLAlchemy ORM)
description: The Album, Segment, and Photo ORM models, and the invariants around nullability, timezones, and pgvector.
tags: [data-model, sqlalchemy, postgres, pgvector, schema]
timestamp: 2026-07-12
---

# Data model (SQLAlchemy ORM)

The [Postgres datastore](/foundations/architecture.md) is the source of truth for everything
the pipeline computes. See [Album definition](/reference/album-definition.md) for how these
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

    # (No lead-reference cache column: lead-face resolution re-uploads the
    # `lead_faces.reference_images` photos each run and relies on Immich's
    # content-dedup for idempotency — see stages/lead_faces.py, step 30.)

    photos: Mapped[list["Photo"]] = relationship(back_populates="album")
    segments: Mapped[list["Segment"]] = relationship(
        back_populates="album", order_by="Segment.position"
    )
    people: Mapped[list["Person"]] = relationship(back_populates="album")


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
    # Faces detected by Immich (no local face-detection dependency). Both NULL
    # until `sync` has fetched faces for this photo (resumability invariant),
    # distinct from a fetched-but-faceless photo (face_count=0, person_ids=[]).
    face_count: Mapped[int | None]                # all detected faces (portrait vs landscape)
    person_ids: Mapped[list[str] | None] = mapped_column(JSON)  # assigned Immich person UUIDs only
    # Immich's manual star rating (−1…5); NULL = unrated (or not yet synced).
    # `select` honors it as a soft curation signal.
    rating: Mapped[int | None]
    # Reverse-geocoded place from Immich's exifInfo; NULL where the photo has no
    # GPS / Immich hasn't geocoded it. `caption` grounding only — no stage
    # *clusters* on it (cluster keeps its GPS/DBSCAN logic).
    city: Mapped[str | None]
    state: Mapped[str | None]
    country: Mapped[str | None]

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


class Person(Base):
    """An album-scoped cache of an Immich `Person` the pipeline has seen.

    Lets `caption` resolve a photo's `person_ids` to display names without a
    second Immich round-trip, and lets a name surviving an Immich merge/rename
    be a re-`sync` away. Populated by `sync`, keyed on the stable Immich person
    id. `is_lead` marks a priority subject resolved from `album.yml`'s
    `lead_faces` (populated in a later enrichment step) — defaults False, so an
    album with no configured leads is unaffected.
    """

    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("album_id", "immich_person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id"), index=True)

    immich_person_id: Mapped[str]
    name: Mapped[str | None]                # None ⇒ recognized but unnamed in Immich
    is_lead: Mapped[bool] = mapped_column(default=False)
```

## Notes

- **`Photo.segment_id` nullable by design.** `NULL` = the album has no segments *or*
  the photo fell outside all of them; distinguish in app logic and warn on the latter
  rather than dropping the photo.
- **`clip_embedding` is a `pgvector` column** — similarity queries
  ([`select`](/stages/select.md) MMR, [`cluster`](/stages/cluster.md) refinement) run
  indexed in the DB, not brute-force numpy. *Testing caveat:* `pgvector` doesn't exist
  in SQLite, so similarity lives behind a substitutable `db/` interface — MMR logic is
  SQLite-testable with a stub, the real query is covered by a `testcontainers` Postgres
  test.
- **`aesthetic_score` + `clip_embedding` are populated on a normal run (build step 31),**
  not just when Immich exposes embeddings — `quality`'s real `ClipAestheticModel`
  (ViT-L/14 + LAION head) writes both, given the opt-in `scoring` extra + fetched
  weights. Without the extra/weights, the flow falls back to the NULL-scoring
  placeholder and both stay `NULL` (`select` has a tested fallback). So `NULL` still
  means "not scored" (base install or a per-photo scoring failure), never "score of 0".
- **Faces are Immich-provided + structured.** `Photo.person_ids` = JSON array of stable
  Immich person UUIDs (assigned faces only), reasoned about structurally by
  [`select`](/stages/select.md), joined to `Person.name` by `caption`. `face_count`
  counts *all* detected faces (incl. unassigned) as a portrait signal. The album-scoped
  `Person` table is the id→name cache. Nullable (`NULL` = faces not synced yet); a
  face-free album leaves `person_ids=[]` and every people-aware stage reduces to
  face-free behavior. See [enrichment](/build/enrichment.md).
- **`rating` + geocoded place** come off data `sync` already fetches (no extra call).
  `rating` (−1…5) is a *soft* `select` boost (never overrides a clearly better shot);
  `city`/`state`/`country` are `caption` grounding only — `cluster` keeps GPS/DBSCAN.
  All nullable ⇒ unrated/un-geocoded album is byte-for-byte as before. OCR deferred —
  see [enrichment](/build/enrichment.md) step 20.
- **`Album.meta` is deliberately dumb** — interpolated into prompts/templates only, no
  stage branches on it. Anything a stage *reasons* about belongs in a real column.
- **All datetimes stored UTC-aware** (`DateTime(timezone=True)`); `definition.py` and
  `sync` normalize to UTC on load, so `cluster` compares in UTC unambiguously even
  across zones. How a photo's source zone is determined: [Open questions](/build/open-questions.md) #3.
- **Nullability is meaningful:** `NULL` = "that stage hasn't run for this photo yet" —
  what makes resumability work. Columns fill left-to-right through the pipeline.
- **Alembic migrations from the start**, never `Base.metadata.create_all()`.
