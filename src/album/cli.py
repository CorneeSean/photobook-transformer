"""The ``photobook-generator`` console entry point — the application's single command.

Two subcommands, one pipeline:

- ``photobook-generator generate <dir>`` — the single-command path (Feature A of
  knowledge/build/single-command-ingestion.md): ingest a local photo directory
  into Immich, materialize an ``album.yml``, then run the pipeline.
- ``photobook-generator run --album <slug>`` — run the pipeline for a
  hand-authored ``data/albums/<slug>/album.yml`` (build step 14).

Both subcommands materialize/consume an ``album.yml`` and then call the **same**
:func:`album.flow.pipeline` — this stays thin (argument parsing plus one call
into :mod:`album.ingest` and/or the pipeline); no pipeline or ingestion logic
lives here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from album.analysis.captioner import Captioner, build_captioner, captioner_names
from album.config import get_settings
from album.flow import pipeline
from album.ingest import (
    _ALBUMS_DIR,
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
        description="Photo album pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_generate_parser(subparsers)
    _add_run_parser(subparsers)

    args = parser.parse_args()
    args.func(args)


def _add_generate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "generate",
        help="Ingest a directory of photos and generate the book (one command)",
        description=(
            "Turn a local directory of photos into an HTML photo book: upload it "
            "to Immich, create an album, and run the pipeline. This is a UX "
            "wrapper over the existing boundary, not a zero-dependency runtime — "
            "the local stack must be up (uv run poe up; uv run poe migrate) and "
            "IMMICH__API_KEY + ANTHROPIC_API_KEY set in .env."
        ),
    )
    parser.add_argument(
        "directory",
        metavar="DIR",
        help="Directory of photos ('.' for the current directory)",
    )
    parser.add_argument(
        "--manifest",
        nargs="?",
        const="ingest.yml",
        metavar="FILE",
        help=(
            "Fan a directory tree into N albums from a manifest "
            "(default: <DIR>/ingest.yml). Turns generate into a multi-album run; "
            "per-album fields (--slug/--title/--source-document/--lead-face) then "
            "live in the manifest, not on the CLI."
        ),
    )
    parser.add_argument("--slug", help="Album slug (default: the directory name, slugified)")
    parser.add_argument("--title", help="Book title (default: the directory name)")
    parser.add_argument(
        "--timezone",
        metavar="IANA",
        help="IANA timezone for undated photos (e.g. Europe/Warsaw)",
    )
    parser.add_argument(
        "--source-document",
        metavar="PDF",
        help="A schedule document (PDF) to extract the album's segments from",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run the pipeline for an already-imported slug (album.yml left untouched)",
    )
    _add_captioner_arg(parser)
    _add_lead_face_arg(parser)
    parser.set_defaults(func=_generate)


def _add_run_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("run", help="Run the pipeline end-to-end for one album")
    parser.add_argument(
        "--album",
        required=True,
        metavar="SLUG",
        help="Album slug (data/albums/<slug>/album.yml)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process an already-run album (e.g. to A/B a different --captioner)",
    )
    _add_captioner_arg(parser)
    _add_lead_face_arg(parser)
    parser.set_defaults(func=_run)


def _add_captioner_arg(parser: argparse.ArgumentParser) -> None:
    """The shared ``--captioner`` selector (both subcommands). Omitted ⇒ the
    ``CAPTIONER`` env default, else ``template`` (the offline, no-API-key default)."""
    parser.add_argument(
        "--captioner",
        choices=captioner_names(),
        help="Caption source (default: the CAPTIONER env var, else 'template')",
    )


def _add_lead_face_arg(parser: argparse.ArgumentParser) -> None:
    """The shared repeatable ``--lead-face`` selector (both subcommands). Each
    occurrence names one priority subject by a local reference photo; they are
    written to the album's ``lead_faces.reference_images`` block. Repeatable
    rather than comma-separated so filenames/paths with commas are unambiguous."""
    parser.add_argument(
        "--lead-face",
        dest="lead_faces",
        metavar="IMAGE",
        action="append",
        help="A local photo naming a priority subject (repeatable for multiple people)",
    )


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


def _generate(args: argparse.Namespace) -> None:
    """``photobook-generator generate <dir>`` — dir of photos → HTML book(s).

    ``--manifest`` fans the directory tree into N albums (one book each); without
    it, the unchanged single-album path runs."""
    if args.manifest is not None:
        _generate_manifest(args)
    else:
        _generate_single(args)


def _generate_single(args: argparse.Namespace) -> None:
    """The single-album path: one directory → one Immich album → one book."""
    try:
        slug = ingest_directory(
            Path(args.directory),
            slug=args.slug,
            title=args.title,
            timezone=args.timezone,
            source_document=Path(args.source_document) if args.source_document else None,
            force=args.force,
            lead_face_images=_lead_face_images(args),
        )
        pipeline(album_slug=slug, captioner=_resolve_captioner(args.captioner), force=args.force)
    except (FileNotFoundError, ValueError, IngestError) as exc:
        # User-fixable errors get a clean message + non-zero exit; anything
        # unexpected keeps its full traceback.
        print(f"photobook-generator generate: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Book written to {_book_path(slug)}")


def _generate_manifest(args: argparse.Namespace) -> None:
    """The fan-out path: a manifest → N albums → N books. Per-album flags are
    rejected (they live in the manifest); a malformed manifest fails fast, but a
    bad *album* is isolated — the run continues and a combined summary reports the
    books written and the albums that failed. Exit is non-zero only if *every*
    album failed (partial success is success)."""
    _reject_per_album_flags(args)
    root = Path(args.directory)
    try:
        # Resolve the captioner up front, inside the guard, so a misconfigured
        # CAPTIONER env fails fast with a clean message *before* any upload — not
        # with a raw traceback after N albums are already in Immich.
        captioner = _resolve_captioner(args.captioner)
        manifest = load_manifest(_resolve_manifest_path(root, args.manifest))
        outcomes = ingest_manifest(root, manifest, timezone=args.timezone, force=args.force)
    except (FileNotFoundError, ValueError, IngestError) as exc:
        print(f"photobook-generator generate --manifest: {exc}", file=sys.stderr)
        sys.exit(1)
    books, failures = _run_album_pipelines(outcomes, captioner, force=args.force)
    _report_manifest_run(books, failures)


def _reject_per_album_flags(args: argparse.Namespace) -> None:
    """Per-album fields belong in the manifest in fan-out mode; setting them on the
    CLI is a clear error, not silent precedence. (``--timezone`` is a global default
    a manifest album may override; ``--captioner``/``--force`` apply to every run.)"""
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
        print(
            f"photobook-generator generate --manifest: {', '.join(offending)} are "
            "per-album; set them in the manifest",
            file=sys.stderr,
        )
        sys.exit(1)


def _resolve_manifest_path(root: Path, manifest_arg: str) -> Path:
    """The manifest file: an absolute ``--manifest`` value verbatim, else resolved
    under the directory (so a bare ``--manifest`` is ``<DIR>/ingest.yml``)."""
    path = Path(manifest_arg)
    return path if path.is_absolute() else root / path


def _run_album_pipelines(
    outcomes: list[AlbumOutcome],
    captioner: Captioner,
    *,
    force: bool,
) -> tuple[list[str], list[tuple[str, str | None]]]:
    """Run the pipeline once per successfully-ingested album, isolating a per-album
    pipeline failure exactly as the ingest fan-out does. Returns the slugs that
    produced a book and the ``(album path, reason)`` of every failure (ingest *or*
    pipeline)."""
    books: list[str] = []
    failures: list[tuple[str, str | None]] = []
    for outcome in outcomes:
        if outcome.slug is None:
            failures.append((outcome.spec.path, outcome.error))
            continue
        try:
            pipeline(album_slug=outcome.slug, captioner=captioner, force=force)
            books.append(outcome.slug)
        except (FileNotFoundError, ValueError) as exc:  # keep real bugs surfacing
            failures.append((outcome.spec.path, str(exc)))
    return books, failures


def _report_manifest_run(books: list[str], failures: list[tuple[str, str | None]]) -> None:
    """Print the combined multi-album summary — the user-facing contract for a
    multi-GB run — and exit non-zero only if *no* book was produced."""
    for slug in books:
        print(f"Book written to {_book_path(slug)}")
    for path, reason in failures:
        print(f"album {path!r} failed: {reason}", file=sys.stderr)
    print(f"{len(books)} book(s) written, {len(failures)} album(s) failed.")
    if not books:  # every album failed
        sys.exit(1)


def _run(args: argparse.Namespace) -> None:
    try:
        lead_face_images = _lead_face_images(args)
        if lead_face_images is not None:
            # Patch the leads onto the existing album.yml before running, so
            # `run --lead-face` names priority subjects without hand-editing YAML.
            patch_lead_faces(_ALBUMS_DIR / args.album / "album.yml", lead_face_images)
        pipeline(
            album_slug=args.album,
            captioner=_resolve_captioner(args.captioner),
            force=args.force,
        )
    except (FileNotFoundError, ValueError, IngestError) as exc:
        # Expected, user-fixable errors (no album.yml, unknown album id, a
        # --lead-face patch onto a missing album, etc.) — a clean one-line
        # message, not a full traceback through Prefect's flow-run machinery.
        # Anything else is a real bug and surfaces in full.
        print(f"photobook-generator run --album {args.album}: {exc}", file=sys.stderr)
        sys.exit(1)
