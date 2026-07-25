"""``PhotoContext`` — a photo's caption grounding, plus the pure prompt builder.

The grounding a photo carries — segment label/place, album metadata, the names of
recognized people, its reverse-geocoded place, and its capture time — is what both
captioners consume: :class:`~album.analysis.captioner.TemplateCaptioner` composes it
directly into an offline caption, and :class:`~album.analysis.captioner.ClaudeCaptioner`
renders it into the text half of its prompt via :func:`format_context`.

Every field is optional, since [`Segment`](/foundations/domain-model.md) itself is —
an album with none of this still produces a caption, just a more generic one
(AGENTS.md rule 1, the genericity guard). This module is DB-free and
side-effect-free; :class:`~album.flow.photo_analyzer.PhotoAnalyzer` owns turning a
``Photo`` row into a :class:`PhotoContext`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PhotoContext:
    """Whatever grounding the album has for one photo — all optional. Frozen, like
    the other value objects (quality-practices.md §1.4): built once, only read."""

    segment_label: str | None = None
    segment_place: str | None = None
    album_meta: dict[str, Any] = field(default_factory=dict)
    # Display names of the *named* people in this photo, resolved from
    # ``Photo.person_ids`` — see ``PhotoAnalyzer._resolve_person_names`` for how,
    # and for why an empty list leaves the caption unchanged (genericity guard).
    person_names: list[str] = field(default_factory=list)
    # The photo's reverse-geocoded place (Immich ``city``/``state``/``country``,
    # joined by ``PhotoAnalyzer._format_place``) — the most specific location
    # grounding, below the segment. ``None`` when the photo has no geocoded place.
    photo_place: str | None = None
    # The photo's capture time (``Photo.timestamp``, UTC), the "when" of the
    # who/where/when a caption reads on. ``None`` for an undated photo.
    capture_time: datetime | None = None


def format_context(context: PhotoContext) -> str:
    """Build the text half of a vision prompt from whatever grounding the album has.
    Standalone and pure so it's directly unit-testable, like ``quality.py``'s
    ``compute_blur_score``. An album with no structure yields just the base line,
    byte-for-byte the no-context prompt (genericity guard)."""
    return "\n".join(["Analyze this photo.", *_grounding_lines(context)])


def _grounding_lines(context: PhotoContext) -> list[str]:
    """The optional grounding lines, in prompt order, omitting any the album lacks."""
    candidates = (
        _segment_line(context),
        _location_line(context),
        _when_line(context),
        _people_line(context),
        _meta_line(context),
    )
    return [line for line in candidates if line is not None]


def _segment_line(context: PhotoContext) -> str | None:
    if not context.segment_label:
        return None
    place = f" in {context.segment_place}" if context.segment_place else ""
    return f"Context: {context.segment_label}{place}."


def _location_line(context: PhotoContext) -> str | None:
    return f"Location: {context.photo_place}." if context.photo_place else None


def _when_line(context: PhotoContext) -> str | None:
    return f"Taken: {format_when(context.capture_time)}." if context.capture_time else None


def _people_line(context: PhotoContext) -> str | None:
    if not context.person_names:
        return None
    return f"People in this photo: {', '.join(context.person_names)}."


def _meta_line(context: PhotoContext) -> str | None:
    return f"Album context: {context.album_meta}." if context.album_meta else None


def format_when(when: datetime) -> str:
    """A capture time as a photo-book-coarse ``"Month YYYY"`` (e.g. ``"May 2026"``).
    Shared by :func:`_when_line` and the offline template captioner so both read the
    same way."""
    return when.strftime("%B %Y")


__all__ = ["PhotoContext", "format_context", "format_when"]
