"""argparse construction for ``photobook-generator`` — argv → ``Namespace``.

Building the parser is this module's whole job: it defines the one ``build``
subcommand and its flags, and stamps the build subparser onto the parsed args as
``args.parser`` so the runner can raise usage errors (exit 2) against it. There is
a single command, so no dispatch table — :func:`album.cli.main` drives the runner
directly.
"""

from __future__ import annotations

import argparse

from album.analysis.captioner import captioner_names
from album.ingest import INGEST_MODES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photobook-generator",
        description="Photo album pipeline — ingest photos and generate HTML book(s).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_build_parser(subparsers)
    return parser


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
        "--caption-language",
        metavar="LANGUAGE",
        help="Output language for prose captions (default: CAPTION_LANGUAGE env, else 'Polish')",
    )
    parser.add_argument(
        "--lead-face",
        dest="lead_faces",
        metavar="IMAGE",
        action="append",
        help="A local photo naming a priority subject (repeatable for multiple people)",
    )
    parser.set_defaults(parser=parser)
