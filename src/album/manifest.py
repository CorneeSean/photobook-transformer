"""Loads and validates a root ``ingest.yml`` manifest — the single source of truth
for folder-structure-driven ingestion (knowledge/build/folder-structure-ingestion.md).

The manifest declares *which* subfolders are album boundaries and *which* are
segment boundaries by relative path, plus per-album overrides. This module is
**pure**: it parses + validates shape and cross-item relationships and nothing
else. Deliberately *not* checked here — on-disk existence of any path and whether
a folder actually contains images: those are filesystem I/O and belong to the
ingest layer (steps 33/34), keeping this loader trivially unit-testable. This is
the same split as :class:`~album.definition.AlbumDefinition` (shape/relationship
validation) vs ``ingest._collect_images`` (filesystem).

A folder-derived segment's ``[start_at, end_at]`` window is derived from photo
timestamps at ingest time, so :class:`SegmentSpec` is deliberately *not*
:class:`~album.definition.SegmentYAML`: it declares only which folder is a
segment (``path`` + optional label/place), never a timestamp.

Genericity is load-bearing: the module, fields, and docstrings speak only
"album / segment / path", matching the genericity rule in
knowledge/reference/folder-structure.md (the same rule as ``agents/segments.py``).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from album.text import slugify


class SegmentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typo safety

    path: str  # relative to the album path; the folder → one Segment
    label: str | None = None  # default at ingest time = titleize(folder name)
    place: str | None = None  # optional; no geocoding here


class AlbumSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str  # relative to the manifest's directory (the root)
    slug: str | None = None  # default at ingest time = slugify(folder name)
    title: str | None = None  # default at ingest time = titleize(folder name)
    timezone: str | None = None  # else the CLI --timezone default
    source_document: str | None = None  # relative to the album path; excludes segments
    lead_faces: list[str] | None = None  # local photos, resolved under the album path
    segments: list[SegmentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape(self) -> AlbumSpec:
        if _is_unsafe_path(self.path):
            raise ValueError(
                f"album path {self.path!r} must be relative and not escape the manifest root",
            )
        if self.source_document is not None and self.segments:
            raise ValueError(
                f"album {self.path!r}: `segments` and `source_document` are mutually exclusive "
                "(one segment source per album)",
            )
        self._validate_segment_paths()
        return self

    def _validate_segment_paths(self) -> None:
        for segment in self.segments:
            if _is_unsafe_path(segment.path):
                raise ValueError(
                    f"segment path {segment.path!r} must be relative and not escape "
                    f"its album {self.path!r}",
                )
        _reject_nested_segments(self.path, self.segments)

    @property
    def effective_slug(self) -> str:
        """The slug this album resolves to — an explicit ``slug`` or the folder
        name slugified. Two albums may not share ``data/albums/<slug>/``, so the
        manifest rejects a collision on this value."""
        return self.slug or slugify(PurePosixPath(self.path).name)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    albums: list[AlbumSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_albums(self) -> Manifest:
        _reject_duplicate(
            [str(PurePosixPath(album.path)) for album in self.albums],
            "album path",
        )
        _reject_duplicate(
            [album.effective_slug for album in self.albums],
            "album slug",
        )
        return self


def _is_unsafe_path(path_str: str) -> bool:
    """True if ``path_str`` is empty, absolute, or contains a ``..`` component —
    i.e. cannot be trusted to stay under the directory it is relative to. Rejecting
    *any* ``..`` component is slightly stricter than "no escape after normalization"
    (it also refuses the interior-but-safe ``a/../b``), but never less safe — no sane
    manifest writes that form."""
    if not path_str.strip():
        return True
    pure = PurePosixPath(path_str)
    return pure.is_absolute() or ".." in pure.parts


def _reject_nested_segments(album_path: str, segments: list[SegmentSpec]) -> None:
    """Reject when one segment path is a path-*component* prefix of another (so
    ``part-1`` is not a prefix of ``part-10``): each photo must map to at most one
    segment, and equal paths nest too (a duplicate). Sorting by components makes a
    prefix adjacent to its extension, so only neighbours need comparing."""
    parts = sorted(PurePosixPath(segment.path).parts for segment in segments)
    for earlier, later in zip(parts, parts[1:], strict=False):
        if _is_component_prefix(earlier, later):
            raise ValueError(
                f"album {album_path!r}: segment paths "
                f"{'/'.join(earlier)!r} and {'/'.join(later)!r} nest",
            )


def _is_component_prefix(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True if ``a``'s path components are a (non-strict) prefix of ``b``'s."""
    return len(a) <= len(b) and b[: len(a)] == a


def _reject_duplicate(values: list[str], noun: str) -> None:
    """Raise on the first repeated value (albums can't share a path or a slug)."""
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {noun} {value!r} — each album must be distinct")
        seen.add(value)


def load_manifest(path: Path) -> Manifest:
    """Read + validate a root ``ingest.yml`` into a :class:`Manifest`. Pure
    shape/relationship validation only — path existence and image presence are
    checked at ingest time. Raises ``ValueError`` (a ``pydantic.ValidationError``,
    which *is* a ``ValueError``) on a malformed manifest, so the CLI can surface
    it before any upload."""
    raw = yaml.safe_load(path.read_text())
    return Manifest.model_validate(raw)
