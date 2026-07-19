"""The ``photobook-generator`` console entry point — one command, ``build``.

``photobook-generator build`` turns photos into HTML book(s). Two phases, each
switchable once the arguments are parsed into an :class:`AlbumSource` strategy:

- **ingest** (default on): a local photo directory → a new Immich album +
  materialized ``album.yml``. ``--manifest`` fans a directory *tree* into N albums
  (one book each); ``--no-ingest`` skips this phase entirely and runs an existing
  ``data/albums/<slug>/album.yml`` as-is (name it with ``--album``).
- **pipeline**: the same :func:`album.flow.pipeline` runs once per prepared album.

So the three shapes are three strategies behind one interface:

    build ./photos                      → DirectoryIngestSource  (ingest one, run)
    build ./photos --manifest           → ManifestIngestSource   (fan-out, run each)
    build --no-ingest --album <slug>    → NoIngestSource         (run an existing album)

This module stays thin: argument parsing, picking the strategy, then driving the
pipeline over what the strategy prepared. No ingestion or pipeline logic lives here.
"""

from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from album.analysis.captioner import Captioner, build_captioner, captioner_names
from album.config import get_settings
from album.flow import pipeline
from album.ingest import (
    _ALBUMS_DIR,
    INGEST_MODES,
    AlbumOutcome,
    IngestError,
    ingest_directory,
    ingest_manifest,
    patch_lead_faces,
)
from album.manifest import load_manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="photobook-generator",
        description="Photo album pipeline — ingest photos and generate HTML book(s).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_build_parser(subparsers)

    args = parser.parse_args()
    args.func(args, args.parser)


def _add_build_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "build",
        help="Ingest a directory of photos and generate the book (one command)",
        description=(
            "Turn photos into an HTML photo book. By default it ingests a local "
            "directory into Immich, then runs the pipeline. Add --manifest to fan a "
            "directory tree into N books, or --no-ingest --album SLUG to skip ingest "
            "and (re)run an existing album.yml. The local stack must be up "
            "(uv run poe up; uv run poe migrate) with IMMICH__API_KEY set; "
            "ANTHROPIC_API_KEY only for the opt-in --captioner claude."
        ),
    )
    parser.add_argument(
        "directory",
        metavar="DIR",
        nargs="?",
        help="Directory of photos to ingest ('.' for the current directory)",
    )
    # How the album(s) to build come to exist — the switchable ingest phase.
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        dest="no_ingest",
        help="Skip ingest; run the pipeline on an existing album (needs --album SLUG)",
    )
    parser.add_argument(
        "--album",
        metavar="SLUG",
        help="With --no-ingest: the existing album to run (data/albums/<slug>/album.yml)",
    )
    parser.add_argument(
        "--manifest",
        nargs="?",
        const="ingest.yml",
        metavar="FILE",
        help=(
            "Fan a directory tree into N albums from a manifest "
            "(default: <DIR>/ingest.yml). Per-album fields "
            "(--slug/--title/--source-document/--lead-face) then live in the manifest."
        ),
    )
    # Per-album ingest options (single-directory ingest only).
    parser.add_argument("--slug", help="Album slug (default: the directory name, slugified)")
    parser.add_argument("--title", help="Book title (default: the directory name)")
    parser.add_argument(
        "--source-document",
        metavar="PDF",
        help="A schedule document (PDF) to extract the album's segments from",
    )
    # Run-level ingest options (apply to every album, including --manifest fan-out).
    parser.add_argument(
        "--timezone",
        metavar="IANA",
        help="IANA timezone for undated photos (e.g. Europe/Warsaw)",
    )
    parser.add_argument(
        "--ingest-mode",
        choices=INGEST_MODES,
        dest="ingest_mode",
        help=(
            "How photos reach Immich (default: the IMMICH__INGEST_MODE env, else "
            "'upload'). 'upload' copies each file in; 'external' scans a read-only "
            "bind mount in place (no copy) — needs IMMICH__EXTERNAL_HOST_ROOT set."
        ),
    )
    # Universal options (every phase / album).
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run an already-built album (e.g. to A/B a different --captioner)",
    )
    parser.add_argument(
        "--captioner",
        choices=captioner_names(),
        help="Caption source (default: the CAPTIONER env var, else 'template')",
    )
    parser.add_argument(
        "--lead-face",
        dest="lead_faces",
        metavar="IMAGE",
        action="append",
        help="A local photo naming a priority subject (repeatable for multiple people)",
    )
    parser.set_defaults(func=_build, parser=parser)


# --- Strategies: the switchable "how the album(s) come to exist" phase ----------


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
        return [_prepared_from_outcome(outcome) for outcome in outcomes]


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


def _prepared_from_outcome(outcome: AlbumOutcome) -> PreparedAlbum:
    """A manifest :class:`~album.ingest.AlbumOutcome` as a :class:`PreparedAlbum`
    (labelled by its manifest path)."""
    return PreparedAlbum(outcome.spec.path, outcome.slug, outcome.error)


def _select_source(args: argparse.Namespace, parser: argparse.ArgumentParser) -> AlbumSource:
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


# --- Driver: run the pipeline over the prepared album(s) and report -------------


def _build(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """``photobook-generator build`` — pick the ingest strategy, prepare the
    album(s), then run the pipeline over each and print a combined summary."""
    try:
        # Resolve the captioner up front, inside the guard, so a misconfigured
        # CAPTIONER env fails cleanly *before* any ingest — not with a raw traceback
        # after N albums are already in Immich.
        captioner = _resolve_captioner(args.captioner)
        prepared = _select_source(args, parser).prepare()
    except (FileNotFoundError, ValueError, IngestError) as exc:
        # Expected, user-fixable errors get a clean message + non-zero exit; anything
        # unexpected keeps its full traceback.
        print(f"photobook-generator build: {exc}", file=sys.stderr)
        sys.exit(1)
    books, failures = _run_pipelines(prepared, captioner, force=args.force)
    _report(books, failures)


def _run_pipelines(
    prepared: list[PreparedAlbum],
    captioner: Captioner,
    *,
    force: bool,
) -> tuple[list[str], list[tuple[str, str | None]]]:
    """Run the pipeline once per prepared album, isolating a per-album pipeline
    failure exactly as ingest isolation does. Returns the slugs that produced a book
    and the ``(label, reason)`` of every failure (preparation *or* pipeline)."""
    books: list[str] = []
    failures: list[tuple[str, str | None]] = []
    for album in prepared:
        if album.slug is None:
            failures.append((album.label, album.error))
            continue
        try:
            pipeline(album_slug=album.slug, captioner=captioner, force=force)
            books.append(album.slug)
        except (FileNotFoundError, ValueError) as exc:  # keep real bugs surfacing
            failures.append((album.label, str(exc)))
    return books, failures


def _report(books: list[str], failures: list[tuple[str, str | None]]) -> None:
    """Print the combined summary (uniform for one album or a fan-out) and exit
    non-zero only if *no* book was produced (partial success is success)."""
    for slug in books:
        print(f"Book written to {_book_path(slug)}")
    for label, reason in failures:
        print(f"album {label!r} failed: {reason}", file=sys.stderr)
    print(f"{len(books)} book(s) written, {len(failures)} album(s) failed.")
    if not books:
        sys.exit(1)


# --- Shared helpers -------------------------------------------------------------


def _lead_face_images(args: argparse.Namespace) -> list[Path] | None:
    """The ``--lead-face`` photos as ``Path``s, or ``None`` when the flag is absent."""
    return [Path(image) for image in args.lead_faces] if args.lead_faces else None


def _resolve_captioner(name: str | None) -> Captioner:
    """The CLI-selected captioner: the ``--captioner`` flag if given, else the
    ``CAPTIONER`` env default. ``build_captioner`` validates either."""
    return build_captioner(name or get_settings().captioner)


def _book_path(slug: str) -> Path:
    """The HTML book the pipeline writes for an album — the single place the output
    layout (``<albums_dir>/<slug>/book/index.html``) is expressed. Mirrors
    HtmlPrinter's default; ``_ALBUMS_DIR`` is the shared album root."""
    return _ALBUMS_DIR / slug / "book" / "index.html"
