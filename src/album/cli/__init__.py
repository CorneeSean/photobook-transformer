"""The ``photobook-generator`` console entry point — one command, ``build``.

``photobook-generator build`` turns photos into HTML book(s). Two phases, each
switchable once the arguments are parsed into an :class:`~album.cli.sources.AlbumSource`
strategy:

- **ingest** (default on): a local photo directory → a new Immich album +
  materialized ``album.yml``. ``--manifest`` fans a directory *tree* into N albums
  (one book each); ``--no-ingest`` skips this phase entirely and runs an existing
  ``data/albums/<slug>/album.yml`` as-is (name it with ``--album``).
- **pipeline**: the same :func:`album.flow.pipeline` runs once per prepared album.

So the three shapes are three strategies behind one interface:

    build ./photos                      → DirectoryIngestSource  (ingest one, run)
    build ./photos --manifest           → ManifestIngestSource   (fan-out, run each)
    build --no-ingest --album <slug>    → NoIngestSource         (run an existing album)

This package stays thin, split by responsibility: :mod:`album.cli.parser` builds
the argument parser, :mod:`album.cli.sources` picks the strategy, and
:mod:`album.cli.runner` drives the pipeline over what the strategy prepared. No
ingestion or pipeline logic lives here.
"""

from __future__ import annotations

from album.cli.parser import build_parser
from album.cli.runner import BuildRunner

__all__ = ["main"]


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    BuildRunner(args, args.parser).run()
