"""Loads and validates an ``album.yml`` file via a ``Definition`` ABC hierarchy.

A generic :class:`Definition` Template Method base owns the shared parse
(read file + validate into :class:`AlbumYAML`); :class:`AlbumDefinition` maps
that onto ``Album`` + ``Segment`` rows and :class:`LeadFacesDefinition` onto the
optional ``lead_faces`` block.

See knowledge/reference/album-definition.md for the on-disk format and timezone rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, model_validator

from album.db.models import Album, Segment


class SegmentYAML(BaseModel):
    label: str
    place: str | None = None
    start_at: datetime
    end_at: datetime


class LeadFacesYAML(BaseModel):
    """The optional ``lead_faces`` block — who the priority subjects are.

    Exactly one input identifies the leads (see knowledge/build/enrichment.md).
    Every input is a *list*, so naming one lead is just the one-element case of
    naming several — there is no separate single-item form:

    - ``reference_images`` — local photos of the leads (one or more, e.g. one per
      subject), each resolved next to ``album.yml`` and uploaded to Immich so its
      detected faces name the people; their faces are unioned. The ``--lead-face``
      CLI flag writes this form (see step 30);
    - ``reference_asset_ids`` — assets already in Immich (no upload); their faces
      are unioned;
    - ``person_ids`` — the Immich people named directly (no photo).

    Resolution to Immich person IDs (and the upload/async-detection wait for
    ``reference_images``) happens later, in ``stages/lead_faces.py`` — this model
    only parses and validates the shape.
    """

    reference_images: list[str] | None = None
    reference_asset_ids: list[str] | None = None
    person_ids: list[str] | None = None

    @model_validator(mode="after")
    def _exactly_one_input(self) -> LeadFacesYAML:
        provided = self._provided_fields()
        if len(provided) != 1:
            raise ValueError(
                "lead_faces must set exactly one of reference_images, "
                f"reference_asset_ids, person_ids (got: {provided or 'none'})",
            )
        return self

    def _provided_fields(self) -> list[str]:
        """Names of the lead-identifying fields that were actually set."""
        candidates = (
            ("reference_images", self.reference_images),
            ("reference_asset_ids", self.reference_asset_ids),
            ("person_ids", self.person_ids),
        )
        return [name for name, value in candidates if value]


class AlbumYAML(BaseModel):
    slug: str
    title: str
    immich_album_id: str | None = None
    timezone: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    segments: list[SegmentYAML] = Field(default_factory=list)
    lead_faces: LeadFacesYAML | None = None
    # Per-album near-duplicate cutoff for `select` (CLIP cosine, 0–1). Omit to use
    # the stage default (0.9); lower it for dense burst/session albums where many
    # near-identical frames sit just under the default. See stages/select.py.
    similarity_threshold: float | None = Field(default=None, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _normalize_and_validate_segments(self) -> AlbumYAML:
        tz = self._resolve_timezone(self.timezone)
        for segment in self.segments:
            segment.start_at = self._to_utc(segment.start_at, tz)
            segment.end_at = self._to_utc(segment.end_at, tz)
            if segment.end_at <= segment.start_at:
                raise ValueError(
                    f"segment {segment.label!r} has end_at ({segment.end_at}) "
                    f"at or before start_at ({segment.start_at})",
                )

        by_start = sorted(self.segments, key=lambda segment: segment.start_at)
        for earlier, later in zip(by_start, by_start[1:], strict=False):
            if earlier.end_at > later.start_at:
                raise ValueError(f"segments {earlier.label!r} and {later.label!r} overlap")

        return self

    @staticmethod
    def _resolve_timezone(timezone: str | None) -> ZoneInfo | None:
        if timezone is None:
            return None
        try:
            return ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown album `timezone` {timezone!r}") from exc

    @staticmethod
    def _to_utc(dt: datetime, tz: ZoneInfo | None) -> datetime:
        if dt.tzinfo is not None:
            return dt.astimezone(UTC)
        if tz is None:
            raise ValueError(
                f"time {dt.isoformat()!r} has no UTC offset and the album has no `timezone` set",
            )
        localized = dt.replace(tzinfo=tz)
        utc = localized.astimezone(UTC)
        if utc.astimezone(tz).replace(tzinfo=None) != dt:
            raise ValueError(
                f"time {dt.isoformat()!r} does not exist in timezone {tz.key!r} "
                "(falls in a DST spring-forward gap)",
            )
        return utc


class Definition[T](ABC):
    """Template Method base for loading an ``album.yml`` into a result ``T``.

    ``load`` owns the **invariant** step every concern shares — read the file and
    validate it into an :class:`AlbumYAML` (the one parse, one set of validation
    errors and UTC-normalization messages) — then delegates the variant work to
    the abstract ``_map`` hook, which projects that validated shape onto each
    subclass's result type. This mirrors ``flow.stages.base.Stage`` (quality-
    practices.md §1.6.1): only the genuinely-shared step is hoisted, the schema
    models (``AlbumYAML`` et al.) stay the validated shape, and the ABC governs
    load/map behaviour rather than the shape (§1.6.4 — a family with real shared
    behaviour to inherit, so nominal ABC, not a structural Protocol).
    """

    def load(self, path: Path) -> T:
        """Read + validate ``path``, then map the validated shape to ``T``."""
        raw = yaml.safe_load(path.read_text())
        parsed = AlbumYAML.model_validate(raw)
        return self._map(parsed)

    @staticmethod
    @abstractmethod
    def _map(parsed: AlbumYAML) -> T:
        """Project the validated ``album.yml`` onto this concern's result type."""


class AlbumDefinition(Definition[Album]):
    """Loads the full album — an unpersisted ``Album`` with its ``Segment`` rows."""

    @staticmethod
    def _map(parsed: AlbumYAML) -> Album:
        album = Album(
            slug=parsed.slug,
            title=parsed.title,
            immich_album_id=parsed.immich_album_id,
            timezone=parsed.timezone,
            meta=parsed.meta,
            similarity_threshold=parsed.similarity_threshold,
        )
        album.segments = [
            Segment(
                position=position,
                label=segment.label,
                place=segment.place,
                start_at=segment.start_at,
                end_at=segment.end_at,
            )
            for position, segment in enumerate(parsed.segments, start=1)
        ]
        return album


class LeadFacesDefinition(Definition[LeadFacesYAML | None]):
    """Loads just the optional ``lead_faces`` block from an ``album.yml``.

    Separate from :class:`AlbumDefinition` because leads are a resolution *input*
    (resolved via Immich in ``flow/stages/lead_faces.py``), not persisted
    ``Album`` state. ``None`` when the album declares no leads.
    """

    @staticmethod
    def _map(parsed: AlbumYAML) -> LeadFacesYAML | None:
        return parsed.lead_faces
