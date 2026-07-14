"""Console entry point for the ``album`` command.

Wired up in step 0 so ``uv run album`` and ``uv run poe run`` resolve; ``run``
lands here in step 14, once ``flow.py`` exists. Kept to argument parsing +
one call into :func:`album.flow.pipeline` — no pipeline logic lives here.
"""

from __future__ import annotations

import argparse
import sys

from album.flow import pipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="album")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the pipeline end-to-end for one album")
    run_parser.add_argument(
        "--album", required=True, metavar="SLUG", help="Album slug (data/albums/<slug>/album.yml)"
    )

    args = parser.parse_args()
    if args.command == "run":
        try:
            pipeline(album_slug=args.album)
        except (FileNotFoundError, ValueError) as exc:
            # Expected, user-fixable errors (no album.yml, unknown album id,
            # etc.) — a clean one-line message, not a full traceback through
            # Prefect's flow-run machinery. Anything else is a real bug and
            # should surface with its full traceback.
            print(f"album run --album {args.album}: {exc}", file=sys.stderr)
            sys.exit(1)
