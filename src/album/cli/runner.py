"""The ``build`` driver: run the pipeline over the prepared album(s) and report.

:class:`BuildRunner` owns one ``photobook-generator build`` invocation — it resolves
the captioner, picks the ingest strategy (:func:`album.cli.sources.select_source`),
prepares the album(s), then runs the pipeline over each and prints a combined
summary. :func:`run_pipelines` is the pure ``prepared → (books, failures)``
transform, kept at module level so it can be driven directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from album.analysis.captioner import Captioner, build_captioner
from album.cli.sources import PreparedAlbum, select_source
from album.config import get_settings
from album.flow import pipeline
from album.ingest import _ALBUMS_DIR, IngestError


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
            print(f"photobook-generator build: {exc}", file=sys.stderr)
            sys.exit(1)
        books, failures = run_pipelines(prepared, captioner, force=self.args.force)
        self._report(books, failures)

    def _report(self, books: list[str], failures: list[tuple[str, str | None]]) -> None:
        """Print the combined summary (uniform for one album or a fan-out) and exit
        non-zero only if *no* book was produced (partial success is success)."""
        for slug in books:
            print(f"Book written to {_book_path(slug)}")
        for label, reason in failures:
            print(f"album {label!r} failed: {reason}", file=sys.stderr)
        print(f"{len(books)} book(s) written, {len(failures)} album(s) failed.")
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
        except (FileNotFoundError, ValueError) as exc:  # keep real bugs surfacing
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
