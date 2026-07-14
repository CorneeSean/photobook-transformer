"""``HtmlPrinter`` — the default printer: a browsable, page-by-page webpage.

Renders the assembled book (see [`base.iter_book_pages`](./base.py)) to a single
self-contained ``index.html`` you can open straight from disk — no server, no
build step, no external service or credentials. That's why it's the pipeline's
default output medium ([`flow.py`](../flow.py)): every other stage's dependency
(Immich, Claude, darktable, Lulu) needs a live boundary this repo can't always
reach, but the HTML book renders from what's already on disk.

The page pulls Tailwind + a few keyframe animations from a CDN for styling and a
bit of live-flow feel (a cover you open into the book, animated page turns, a
progress bar, and an "Auto" switch that steps a page every 5s to the end), and a
small vanilla-JS navigator for keyboard/click paging — but it stays *one static
file*, deliberately not a web app. Photos are referenced by
a path **relative to the written HTML file** (computed from each photo's
``rendered_path``), so the browser loads the rendered images straight from disk.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from album.db.models import Album
from album.printers.base import BookPage, PrintResult, iter_book_pages

_log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"


class HtmlPrinter:
    """Renders an album's assembled book to a static ``index.html``.

    ``output_dir`` mirrors ``assemble``'s: the book is written to
    ``<output_dir>/<slug>/book/index.html``. Implements
    :class:`~album.printers.base.Printer`.
    """

    def __init__(
        self, output_dir: str | Path = "data/albums", *, templates_dir: Path = _TEMPLATES_DIR
    ) -> None:
        self._output_dir = Path(output_dir)
        self._env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(["html"]),
        )

    def print(self, album: Album, session: Session) -> PrintResult:
        pages = iter_book_pages(album, session)

        book_dir = self._output_dir / album.slug / "book"
        book_dir.mkdir(parents=True, exist_ok=True)
        index_path = book_dir / "index.html"

        html = self._env.get_template("book/viewer.html").render(
            album_title=album.title,
            pages=[_page_ctx(page, book_dir) for page in pages],
        )
        _ = index_path.write_text(html, encoding="utf-8")

        _log.info("html book written to %s (%d pages)", index_path, len(pages))
        return PrintResult(output_ref=str(index_path), detail={"pages": len(pages)})


def _page_ctx(page: BookPage, book_dir: Path) -> dict[str, Any]:
    """A page's template context, with each photo's on-disk path made relative to
    the written HTML file so the browser can load it directly."""
    return {
        "number": page.number,
        "starts_segment": page.starts_segment,
        "segment_label": page.segment_label,
        "segment_place": page.segment_place,
        "photos": [
            {"src": _relative_src(photo.image_path, book_dir), "caption": photo.caption}
            for photo in page.photos
        ],
    }


def _relative_src(image_path: str, book_dir: Path) -> str:
    """Path to ``image_path`` relative to ``book_dir`` (where index.html lives),
    using POSIX separators so it works as a browser URL on any platform."""
    rel = os.path.relpath(os.path.abspath(image_path), start=os.path.abspath(book_dir))
    return Path(rel).as_posix()
