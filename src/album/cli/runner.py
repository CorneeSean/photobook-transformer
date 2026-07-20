"""The ``build`` driver: run the pipeline over the prepared album(s) and report.

:class:`BuildRunner` owns one ``photobook-generator build`` invocation — it resolves
the captioner, picks the ingest strategy (:func:`album.cli.sources.select_source`),
prepares the album(s), then runs the pipeline over each and reports a combined
summary. :func:`run_pipelines` is the pure ``prepared → (books, failures)``
transform, kept at module level so it can be driven directly.

The user-facing summary is emitted through the dedicated ``album.cli.output`` logger
(not ``print``) so verbosity is controllable, while preserving today's contract:
success lines on stdout, failures on stderr, no prefix.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from album.analysis.captioner import Captioner, build_captioner
from album.cli.sources import PreparedAlbum, select_source
from album.config import get_settings
from album.flow import pipeline
from album.flow.stages.base import StageTotalFailure
from album.ingest import _ALBUMS_DIR, IngestError

# The `build` command's user-facing output goes through a dedicated logger rather
# than bare `print`, so verbosity is controllable — while preserving today's exact
# contract: the success summary on stdout, failures on stderr, no log prefix. INFO
# → stdout, WARNING+ → stderr (see _configure_cli_output).
_out = logging.getLogger("album.cli.output")


class _MaxLevelFilter(logging.Filter):
    """Passes only records at or below ``level`` — keeps INFO on the stdout handler
    so WARNING/ERROR fall through to the stderr handler alone (no double-print)."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self._level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self._level


class _NamedStreamHandler(logging.Handler):
    """Writes to ``sys.stdout``/``sys.stderr`` resolved *at emit time* (by name), so
    pytest's ``capsys`` — which swaps those objects — still captures CLI output and
    the stdout/stderr split is preserved."""

    def __init__(self, stream_name: str) -> None:
        super().__init__()
        self._stream_name = stream_name

    def emit(self, record: logging.LogRecord) -> None:
        stream = getattr(sys, self._stream_name)
        try:
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:  # pragma: no cover - matches logging.Handler's own contract
            self.handleError(record)


def _configure_cli_output(logger: logging.Logger) -> None:
    """Reproduce the build command's exact stdout/stderr split with no prefix: INFO →
    stdout, WARNING+ → stderr. Idempotent, and non-propagating so app diagnostics
    logging can't double-print (or re-format) user-facing output."""
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.propagate = False
    plain = logging.Formatter("%(message)s")

    stdout = _NamedStreamHandler("stdout")
    stdout.setLevel(logging.INFO)
    stdout.addFilter(_MaxLevelFilter(logging.INFO))
    stdout.setFormatter(plain)

    stderr = _NamedStreamHandler("stderr")
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(plain)

    logger.addHandler(stdout)
    logger.addHandler(stderr)


_configure_cli_output(_out)


class BuildRunner:
    """One ``photobook-generator build`` invocation: prepare the album(s), run the
    pipeline over each, and report the combined result."""

    def __init__(self, args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
        self.args = args
        self.parser = parser

    def run(self) -> None:
        """Pick the ingest strategy, prepare the album(s), then run the pipeline over
        each and print a combined summary."""
        try:
            # Resolve the captioner up front, inside the guard, so a misconfigured
            # CAPTIONER env fails cleanly *before* any ingest — not with a raw traceback
            # after N albums are already in Immich.
            captioner = _resolve_captioner(self.args.captioner)
            prepared = select_source(self.args, self.parser).prepare()
        except (FileNotFoundError, ValueError, IngestError) as exc:
            # Expected, user-fixable errors get a clean message + non-zero exit; anything
            # unexpected keeps its full traceback.
            _out.error("photobook-generator build: %s", exc)
            sys.exit(1)
        books, failures = run_pipelines(prepared, captioner, force=self.args.force)
        self._report(books, failures)

    def _report(self, books: list[str], failures: list[tuple[str, str | None]]) -> None:
        """Emit the combined summary (uniform for one album or a fan-out) and exit
        non-zero only if *no* book was produced (partial success is success)."""
        for slug in books:
            _out.info("Book written to %s", _book_path(slug))
        for label, reason in failures:
            _out.error("album %r failed: %s", label, reason)
        _out.info("%d book(s) written, %d album(s) failed.", len(books), len(failures))
        if not books:
            sys.exit(1)


def run_pipelines(
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
        except (FileNotFoundError, ValueError, StageTotalFailure) as exc:
            # Expected per-album failures — a bad album.yml, or a stage where every
            # attempted photo failed (Immich down, model missing). Isolated so a
            # fan-out's healthy siblings still build; other exceptions keep surfacing
            # as real bugs.
            failures.append((album.label, str(exc)))
    return books, failures


def _resolve_captioner(name: str | None) -> Captioner:
    """The CLI-selected captioner: the ``--captioner`` flag if given, else the
    ``CAPTIONER`` env default. ``build_captioner`` validates either."""
    return build_captioner(name or get_settings().captioner)


def _book_path(slug: str) -> Path:
    """The HTML book the pipeline writes for an album — the single place the output
    layout (``<albums_dir>/<slug>/book/index.html``) is expressed. Mirrors
    HtmlPrinter's default; ``_ALBUMS_DIR`` is the shared album root."""
    return _ALBUMS_DIR / slug / "book" / "index.html"
