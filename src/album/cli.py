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

from album.flow import pipeline
from album.ingest import _ALBUMS_DIR, IngestError, ingest_directory


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="photobook-generator", description="Photo album pipeline."
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
        "directory", metavar="DIR", help="Directory of photos ('.' for the current directory)"
    )
    parser.add_argument("--slug", help="Album slug (default: the directory name, slugified)")
    parser.add_argument("--title", help="Book title (default: the directory name)")
    parser.add_argument(
        "--timezone", metavar="IANA", help="IANA timezone for undated photos (e.g. Europe/Warsaw)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run the pipeline for an already-imported slug (album.yml left untouched)",
    )
    parser.set_defaults(func=_generate)


def _add_run_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("run", help="Run the pipeline end-to-end for one album")
    parser.add_argument(
        "--album", required=True, metavar="SLUG", help="Album slug (data/albums/<slug>/album.yml)"
    )
    parser.set_defaults(func=_run)


def _generate(args: argparse.Namespace) -> None:
    """``photobook-generator generate <dir>`` — dir of photos → HTML book, one command."""
    try:
        slug = ingest_directory(
            Path(args.directory),
            slug=args.slug,
            title=args.title,
            timezone=args.timezone,
            force=args.force,
        )
        pipeline(album_slug=slug)
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
        pipeline(album_slug=args.album)
    except (FileNotFoundError, ValueError) as exc:
        # Expected, user-fixable errors (no album.yml, unknown album id, etc.) —
        # a clean one-line message, not a full traceback through Prefect's
        # flow-run machinery. Anything else is a real bug and surfaces in full.
        print(f"photobook-generator run --album {args.album}: {exc}", file=sys.stderr)
        sys.exit(1)
