"""Loads and validates an ``album.yml`` file into ``Album`` + ``Segment`` rows.

See knowledge/reference/album-definition.md for the on-disk format and timezone rules.
"""

from __future__ import annotations

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

    Exactly one input identifies the leads (see knowledge/build/enrichment.md):

    - ``reference_image`` — a local photo of the leads, resolved next to
      ``album.yml``, uploaded to Immich so its detected faces name the people;
    - ``reference_asset_id`` — an asset already in Immich (no upload);
    - ``person_ids`` — the Immich people named directly (no photo).

    Resolution to Immich person IDs (and the upload/async-detection wait for
    ``reference_image``) happens later, in ``stages/lead_faces.py`` — this model
    only parses and validates the shape.
    """

    reference_image: str | None = None
    reference_asset_id: str | None = None
    person_ids: list[str] | None = None

    @model_validator(mode="after")
    def _exactly_one_input(self) -> LeadFacesYAML:
        provided = self._provided_fields()
        if len(provided) != 1:
            raise ValueError(
                "lead_faces must set exactly one of reference_image, "
                f"reference_asset_id, person_ids (got: {provided or 'none'})"
            )
        return self

    def _provided_fields(self) -> list[str]:
        """Names of the lead-identifying fields that were actually set."""
        candidates = (
            ("reference_image", self.reference_image),
            ("reference_asset_id", self.reference_asset_id),
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

    @model_validator(mode="after")
    def _normalize_and_validate_segments(self) -> AlbumYAML:
        tz = self._resolve_timezone(self.timezone)
        for segment in self.segments:
            segment.start_at = self._to_utc(segment.start_at, tz)
            segment.end_at = self._to_utc(segment.end_at, tz)
            if segment.end_at <= segment.start_at:
                raise ValueError(
                    f"segment {segment.label!r} has end_at ({segment.end_at}) "
                    f"at or before start_at ({segment.start_at})"
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
                f"time {dt.isoformat()!r} has no UTC offset and the album has no `timezone` set"
            )
        localized = dt.replace(tzinfo=tz)
        utc = localized.astimezone(UTC)
        if utc.astimezone(tz).replace(tzinfo=None) != dt:
            raise ValueError(
                f"time {dt.isoformat()!r} does not exist in timezone {tz.key!r} "
                "(falls in a DST spring-forward gap)"
            )
        return utc


def load_album_definition(path: Path) -> Album:
    """Parse and validate ``path`` into an unpersisted ``Album`` with its segments."""
    raw = yaml.safe_load(path.read_text())
    parsed = AlbumYAML.model_validate(raw)

    album = Album(
        slug=parsed.slug,
        title=parsed.title,
        immich_album_id=parsed.immich_album_id,
        timezone=parsed.timezone,
        meta=parsed.meta,
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


def load_lead_faces(path: Path) -> LeadFacesYAML | None:
    """Parse just the ``lead_faces`` block from an ``album.yml``.

    Returned separately from :func:`load_album_definition` because leads are a
    resolution *input* (resolved via Immich in ``stages/lead_faces.py``), not
    persisted ``Album`` state. ``None`` when the album declares no leads.
    """
    raw = yaml.safe_load(path.read_text())
    return AlbumYAML.model_validate(raw).lead_faces
