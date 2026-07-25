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

import logging
import os

from album.cli.parser import build_parser
from album.cli.runner import BuildRunner

__all__ = ["main"]


def _configure_diagnostics_logging() -> None:
    """Let the app's diagnostic logs (stage start/done, the per-photo
    ``ProgressLogger`` heartbeat) reach Prefect's console handler.

    These come from the ``album.flow.stages.*`` stdlib loggers. Prefect installs a
    ``PrefectConsoleHandler`` on the *root* logger but leaves root at WARNING, and
    ``album.*`` sets no level of its own — so its effective level inherits WARNING
    and every INFO record was filtered out before reaching that handler. Nothing
    showed in a real run.

    Fix: lower the ``album`` logger to INFO (``LOG_LEVEL`` overrides). The records
    then pass and propagate to Prefect's root handler, which colorizes by level and
    already owns the terminal — so app diagnostics get Prefect's exact colored
    format for free, with no second handler and no duplicate line. Idempotent (a
    plain ``setLevel``), so repeated ``main()`` calls are harmless.
    """
    logging.getLogger("album").setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def main() -> None:
    _configure_diagnostics_logging()
    parser = build_parser()
    args = parser.parse_args()
    BuildRunner(args, args.parser).run()
