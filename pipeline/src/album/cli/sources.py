"""The switchable ingest phase: the ``AlbumSource`` strategies and, given the parsed
args, which one to build.

Each strategy prepares the album(s) the pipeline then runs — by ingesting a
directory, fanning a manifest out, or using an existing ``album.yml`` as-is — and
returns them in a uniform shape (:class:`PreparedAlbum`) so a single driver runs
and reports them the same way. :func:`select_source` validates the argument
combination and picks the one strategy it selects.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from album.ingest import (
    _ALBUMS_DIR,
    AlbumOutcome,
    ingest_directory,
    ingest_manifest,
    patch_lead_faces,
)
from album.manifest import load_manifest


@dataclass(frozen=True)
class PreparedAlbum:
    """One album ready for the pipeline: exactly one of ``slug`` (prepared — run it)
    or ``error`` (preparation was isolated and skipped) is set. ``label`` is what to
    call it in the summary (the slug, directory, or manifest path)."""

    label: str
    slug: str | None
    error: str | None


class AlbumSource(ABC):
    """The switchable ingest phase, chosen once from the parsed args. Each strategy
    prepares the album(s) the pipeline then runs — by ingesting a directory, fanning
    a manifest out, or using an existing ``album.yml`` as-is — and returns them in a
    uniform shape so a single driver runs and reports them the same way.

    Single-album strategies raise on failure (the caller's error guard turns it into
    a clean message + exit 1); the manifest strategy isolates *per-album* failures
    into :class:`PreparedAlbum` so one bad album never sinks the whole fan-out."""

    @abstractmethod
    def prepare(self) -> list[PreparedAlbum]:
        """Ingest / resolve the album(s) and return them ready for the pipeline."""


@dataclass(frozen=True)
class DirectoryIngestSource(AlbumSource):
    """Default: ingest one photo directory into a new Immich album, then run it."""

    directory: Path
    slug: str | None
    title: str | None
    timezone: str | None
    source_document: Path | None
    lead_face_images: list[Path] | None
    force: bool
    ingest_mode: str | None

    def prepare(self) -> list[PreparedAlbum]:
        slug = ingest_directory(
            self.directory,
            slug=self.slug,
            title=self.title,
            timezone=self.timezone,
            source_document=self.source_document,
            force=self.force,
            lead_face_images=self.lead_face_images,
            ingest_mode=self.ingest_mode,
        )
        return [PreparedAlbum(str(self.directory), slug, None)]


@dataclass(frozen=True)
class ManifestIngestSource(AlbumSource):
    """``--manifest``: fan a directory tree into N albums under one shared client,
    isolating per-album ingest failures so a bad album doesn't sink the run."""

    root: Path
    manifest_path: Path
    timezone: str | None
    force: bool
    ingest_mode: str | None

    def prepare(self) -> list[PreparedAlbum]:
        manifest = load_manifest(self.manifest_path)
        outcomes = ingest_manifest(
            self.root,
            manifest,
            timezone=self.timezone,
            force=self.force,
            ingest_mode=self.ingest_mode,
        )
        return [prepared_from_outcome(outcome) for outcome in outcomes]


@dataclass(frozen=True)
class NoIngestSource(AlbumSource):
    """``--no-ingest``: run the pipeline on an existing ``album.yml`` as-is, patching
    ``--lead-face`` onto it first (so leads can be (re)named without editing YAML)."""

    slug: str
    lead_face_images: list[Path] | None

    def prepare(self) -> list[PreparedAlbum]:
        if self.lead_face_images is not None:
            patch_lead_faces(_ALBUMS_DIR / self.slug / "album.yml", self.lead_face_images)
        return [PreparedAlbum(self.slug, self.slug, None)]


def prepared_from_outcome(outcome: AlbumOutcome) -> PreparedAlbum:
    """A manifest :class:`~album.ingest.AlbumOutcome` as a :class:`PreparedAlbum`
    (labelled by its manifest path)."""
    return PreparedAlbum(outcome.spec.path, outcome.slug, outcome.error)


def select_source(args: argparse.Namespace, parser: argparse.ArgumentParser) -> AlbumSource:
    """Validate the argument combination and build the one strategy it selects.
    Misuse (a per-album flag in fan-out mode, --album without --no-ingest, a missing
    directory, …) is an argparse usage error (exit 2), raised via ``parser.error``."""
    if args.no_ingest:
        return _no_ingest_source(args, parser)
    if args.album is not None:
        parser.error("--album is only valid with --no-ingest; use --slug to name an ingested album")
    if args.directory is None:
        parser.error("a photo directory (DIR) is required, or use --no-ingest --album SLUG")
    return _ingest_source(args, Path(args.directory), parser)


def _ingest_source(
    args: argparse.Namespace, root: Path, parser: argparse.ArgumentParser
) -> AlbumSource:
    """The ingest strategy for a photo directory: fan-out when ``--manifest`` is
    given (rejecting per-album flags), else a single-directory ingest."""
    if args.manifest is not None:
        _reject_per_album_flags(args, parser)
        return ManifestIngestSource(
            root=root,
            manifest_path=_resolve_manifest_path(root, args.manifest),
            timezone=args.timezone,
            force=args.force,
            ingest_mode=args.ingest_mode,
        )
    return DirectoryIngestSource(
        directory=root,
        slug=args.slug,
        title=args.title,
        timezone=args.timezone,
        source_document=Path(args.source_document) if args.source_document else None,
        lead_face_images=_lead_face_images(args),
        force=args.force,
        ingest_mode=args.ingest_mode,
    )


def _no_ingest_source(args: argparse.Namespace, parser: argparse.ArgumentParser) -> NoIngestSource:
    """Build the ``--no-ingest`` strategy, rejecting the ingest-only flags that make
    no sense without an ingest phase (a positional directory, --manifest, and the
    per-album/run-level ingest options)."""
    if args.album is None:
        parser.error("--no-ingest needs --album SLUG (the existing album to run)")
    offending = [
        name
        for name, value in (
            ("DIR", args.directory),
            ("--manifest", args.manifest),
            ("--slug", args.slug),
            ("--title", args.title),
            ("--timezone", args.timezone),
            ("--source-document", args.source_document),
            ("--ingest-mode", args.ingest_mode),
        )
        if value is not None
    ]
    if offending:
        parser.error(f"--no-ingest ignores ingest options: {', '.join(offending)}")
    return NoIngestSource(args.album, _lead_face_images(args))


def _reject_per_album_flags(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Per-album fields belong in the manifest in fan-out mode; setting them on the
    CLI is a usage error, not silent precedence. (``--timezone``/``--ingest-mode``
    are run-level defaults, and ``--captioner``/``--force`` apply to every run.)"""
    offending = [
        name
        for name, value in (
            ("--slug", args.slug),
            ("--title", args.title),
            ("--source-document", args.source_document),
            ("--lead-face", args.lead_faces),
        )
        if value
    ]
    if offending:
        parser.error(f"{', '.join(offending)} are per-album; set them in the manifest")


def _resolve_manifest_path(root: Path, manifest_arg: str) -> Path:
    """The manifest file: an absolute ``--manifest`` value verbatim, else resolved
    under the directory (so a bare ``--manifest`` is ``<DIR>/ingest.yml``)."""
    path = Path(manifest_arg)
    return path if path.is_absolute() else root / path


def _lead_face_images(args: argparse.Namespace) -> list[Path] | None:
    """The ``--lead-face`` photos as ``Path``s, or ``None`` when the flag is absent."""
    return [Path(image) for image in args.lead_faces] if args.lead_faces else None
