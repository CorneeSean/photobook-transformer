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

    # --- populated by `fulfill` ---
    # A printer-agnostic reference to the delivered output: a Lulu print-job id,
    # a `file://`/path to a generated webpage, etc. — whatever the chosen
    # `Printer` returns (see album/printers/). NULL until the book is printed;
    # its presence is what makes `fulfill` idempotent (skip re-printing unless
    # force=True — see stages/fulfill.py).
    output_ref: Mapped[str | None] = mapped_column(default=None)

    # Default IANA timezone (e.g. "Europe/Podgorica") for interpreting any
    # segment or photo time that arrives without an explicit UTC offset. Used
    # only at load/sync time to normalize everything to UTC in the DB; no stage
    # reasons about it afterwards, because every stored datetime is already UTC.
    timezone: Mapped[str | None] = mapped_column(default=None)

    # Free-form escape hatch for album-specific context that stages don't
    # reason about, but caption prompts and templates may interpolate.
    # e.g. {"occasion": "honeymoon", "names": ["Alex", "Sam"]}
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Per-album near-duplicate cutoff for `select`: a candidate whose CLIP cosine
    # similarity to an already-picked photo in the same scene exceeds this is
    # dropped. NULL ⇒ the stage's default (0.9). Lower it for dense burst/session
    # albums where many near-identical frames sit just under the default and would
    # otherwise all survive; see stages/select.py.
    similarity_threshold: Mapped[float | None] = mapped_column(default=None)

    # --- populated by `assemble` ---
    # Path to the rendered print-ready PDF (e.g. "data/albums/<slug>/assembled.pdf").
    # Integration contract with `fulfill`, which reads this to submit the print
    # order — the name and semantics are agreed across both stages; don't rename.
    assembled_pdf_path: Mapped[str | None] = mapped_column(default=None)

    photos: Mapped[list[Photo]] = relationship(back_populates="album")
    segments: Mapped[list[Segment]] = relationship(
        back_populates="album",
        order_by="Segment.position",
    )
    people: Mapped[list[Person]] = relationship(back_populates="album")


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
    # Faces detected by Immich (no local face-detection dependency). Both NULL
    # until `sync` has fetched faces for this photo — the resumability invariant.
    # `face_count` counts *all* detected faces (portrait-vs-landscape signal);
    # `person_ids` holds only the *assigned* faces' stable Immich person UUIDs,
    # which `select` reasons about structurally (coverage) and `caption` joins to
    # Person.name. A recognized-but-unassigned face counts toward face_count but
    # contributes no person id.
    face_count: Mapped[int | None] = mapped_column(default=None)
    person_ids: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    # Immich's manual star rating (−1…5; NULL = unrated, or `sync` hasn't run).
    # `select` honors it as a soft curation signal — a starred photo ranks
    # higher, an explicitly rejected one lower. See knowledge/build/enrichment.md
    # step 20.
    rating: Mapped[int | None] = mapped_column(default=None)
    # Reverse-geocoded place from Immich's `exifInfo` (NULL where the photo has
    # no GPS, or Immich hasn't geocoded it). Grounding for `caption`; no stage
    # *clusters* on it (cluster keeps its GPS/DBSCAN logic). See enrichment.md
    # step 20.
    city: Mapped[str | None] = mapped_column(default=None)
    state: Mapped[str | None] = mapped_column(default=None)
    country: Mapped[str | None] = mapped_column(default=None)

    # --- populated by `quality` ---
    blur_score: Mapped[float | None] = mapped_column(default=None)  # Laplacian variance
    aesthetic_score: Mapped[float | None] = mapped_column(default=None)  # LAION predictor
    # pgvector column — indexed cosine similarity, used by `cluster` + `select`.
    # Only populated if Immich doesn't expose its own embeddings (see open Q1).
    clip_embedding: Mapped[list[float] | None] = mapped_column(Vector(768), default=None)

    # --- populated by `cluster` ---
    segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("segments.id"),
        index=True,
        default=None,
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


class Person(Base):
    """An album-scoped cache of an Immich ``Person`` the pipeline has seen.

    Lets `caption` resolve a photo's ``person_ids`` to display names without a
    second Immich round-trip, and lets a name surviving an Immich merge/rename be
    a re-``sync`` away. Populated by `sync`, keyed on the stable Immich person id.
    ``is_lead`` marks a priority subject resolved from ``album.yml``'s
    ``lead_faces`` (populated in a later step); it defaults False, so an album
    with no configured leads behaves exactly as before.
    """

    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("album_id", "immich_person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id"), index=True)

    immich_person_id: Mapped[str]
    # None ⇒ recognized but unnamed in Immich.
    name: Mapped[str | None] = mapped_column(default=None)
    is_lead: Mapped[bool] = mapped_column(default=False)  # a priority subject (from lead_faces)

    album: Mapped[Album] = relationship(back_populates="people")
