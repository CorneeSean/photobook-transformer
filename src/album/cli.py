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
from album.ingest import _ALBUMS_DIR, IngestError, ingest_directory, patch_lead_faces


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


def _generate(args: argparse.Namespace) -> None:
    """``photobook-generator generate <dir>`` — dir of photos → HTML book, one command."""
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

    # Mirrors HtmlPrinter's default output layout (<albums_dir>/<slug>/book/index.html);
    # _ALBUMS_DIR is the shared album root the pipeline also writes under.
    print(f"Book written to {_ALBUMS_DIR / slug / 'book' / 'index.html'}")


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
