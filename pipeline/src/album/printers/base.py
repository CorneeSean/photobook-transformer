"""The ``Printer`` boundary — how a finished, laid-out album gets *delivered*.

[`assemble`](../stages/assemble.py) decides the book's page layout (which photos
land on which page, in what order, with which section breaks) and records it on
the ``Photo`` rows (``page_number``). What happens to that finished book — a
physical copy ordered from a print service, a browsable local webpage, a PDF
dropped somewhere — is a separate, swappable concern. That's a :class:`Printer`,
which renders the concrete medium on demand from that layout.

The [`fulfill`](../stages/fulfill.py) stage owns *no* delivery policy of its own:
it just calls the injected printer and stores the printer-agnostic reference the
printer returns on ``Album.output_ref``. Concrete printers live beside this
module — :class:`~album.printers.html.HtmlPrinter` (the default; no external
service, renders a webpage from the on-disk photos) and
:class:`~album.printers.lulu.LuluPrinter` (opt-in; renders the master PDF and
submits it to Lulu's Print API for a physical book).

``iter_book_pages`` is the shared reader that reconstructs the ordered pages from
the DB, so every medium-rendering printer lays the book out the same way
``assemble`` paginated it, rather than re-deriving pagination per printer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo, Segment


@dataclass(frozen=True)
class BookPhoto:
    """One placed photo: where its rendered file is on disk, and its caption."""

    image_path: str
    caption: str | None


@dataclass(frozen=True)
class BookPage:
    """One page of the finished book: the photos ``assemble`` placed on it, plus
    the segment it belongs to (``None`` for an album with no segments, or before
    the first segment). ``starts_segment`` is True on the first page of a new
    segment, so a renderer can draw a section divider without needing
    ``assemble``'s separate section pages."""

    number: int
    photos: list[BookPhoto]
    segment_label: str | None = None
    segment_place: str | None = None
    starts_segment: bool = False


@dataclass
class PrintResult:
    """What a :class:`Printer` produced. ``output_ref`` is medium-agnostic — a
    Lulu order id, a path to a generated ``index.html``, etc. — and is stored on
    ``Album.output_ref`` by the ``fulfill`` stage."""

    output_ref: str
    detail: dict[str, object] = field(default_factory=dict)


# runtime_checkable so flow.py's pipeline() (a Prefect @flow) can name this type
# in its own signature: Prefect builds a Pydantic parameter schema for a flow
# even with validate_parameters=False, and that schema generation runs an
# isinstance check against every annotation. (@task has no such step.)
@runtime_checkable
class Printer(Protocol):
    """Delivers a finished, assembled album in one medium.

    Given the ``Album`` and an open ``Session`` (to read the placed photos —
    ``iter_book_pages`` — that a print service or a webpage renders from),
    produce the deliverable and return a :class:`PrintResult`.
    Idempotency and the ``output_ref`` write are the ``fulfill`` stage's job,
    not the printer's — a printer just produces.
    """

    def print(self, album: Album, session: Session) -> PrintResult: ...


class NotAssembledError(ValueError):
    """The album hasn't been laid out yet, so there's nothing to print.

    Raised by ``iter_book_pages`` (and the printers that render from it) when no
    selected photo has a ``page_number``. Run ``assemble`` before ``fulfill``.
    """


def iter_book_pages(album: Album, session: Session) -> list[BookPage]:
    """Reconstruct the book's pages, in order, from ``assemble``'s output.

    Groups the album's placed photos (``selected`` and ``page_number`` set) by
    page number, ordered by page number. Each :class:`BookPage` carries its
    photos and the segment they belong to; ``starts_segment`` marks the first
    page of each new segment so a renderer can draw a divider. An album with no
    segments simply yields pages whose ``segment_label`` is ``None`` — the
    genericity fallback (nothing here assumes an occasion or a segment).

    Raises :class:`NotAssembledError` if no photo is placed yet.
    """
    placed = _fetch_placed_photos(album, session)
    segments_by_id = {seg.id: seg for seg in album.segments}
    seen_segment_ids: set[int | None] = set()
    pages: list[BookPage] = []
    for number, group in groupby(placed, key=lambda p: p.page_number):
        page_photos = list(group)
        pages.append(_build_page(number, page_photos, segments_by_id, seen_segment_ids))
    return pages


def _fetch_placed_photos(album: Album, session: Session) -> list[Photo]:
    """The album's selected, paginated photos, in page order — ``assemble``'s output."""
    placed = list(
        session.scalars(
            select(Photo)
            .where(Photo.album_id == album.id, Photo.selected, Photo.page_number.is_not(None))
            .order_by(Photo.page_number),
        ),
    )
    if not placed:
        raise NotAssembledError(
            f"album {album.slug!r} has no placed photos — run `assemble` before printing",
        )
    return placed


def _build_page(
    number: int | None,
    page_photos: list[Photo],
    segments_by_id: dict[int, Segment],
    seen_segment_ids: set[int | None],
) -> BookPage:
    """One :class:`BookPage` for a single page number's photos, marking whether
    it opens a new segment and recording that segment as seen."""
    segment_id = page_photos[0].segment_id
    segment = _segment_for(segment_id, segments_by_id)
    starts_segment = segment is not None and segment_id not in seen_segment_ids
    seen_segment_ids.add(segment_id)
    return BookPage(
        number=number,  # pyright: ignore[reportArgumentType] -- filtered is_not(None) above
        photos=[_book_photo(p) for p in page_photos],
        segment_label=segment.label if segment is not None else None,
        segment_place=segment.place if segment is not None else None,
        starts_segment=starts_segment,
    )


def _segment_for(segment_id: int | None, segments_by_id: dict[int, Segment]) -> Segment | None:
    """The segment a photo belongs to, or ``None`` for an unsegmented photo."""
    if segment_id is None:
        return None
    return segments_by_id.get(segment_id)


def _book_photo(photo: Photo) -> BookPhoto:
    if photo.rendered_path is None:
        # assemble already guards this (it refuses to place an unrendered photo),
        # so reaching here means an out-of-band DB edit — fail loudly rather than
        # emit a page with a broken <img>.
        raise NotAssembledError(
            f"placed photo {photo.id} has no rendered_path — run `render`/`assemble` first",
        )
    return BookPhoto(image_path=photo.rendered_path, caption=photo.caption)
