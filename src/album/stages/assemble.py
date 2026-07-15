"""``assemble`` — lay out selected photos into Jinja2 page templates and render
a print-ready PDF via WeasyPrint, scoped by ``album_id``.

Reads ``selected=True`` [photos](/reference/data-model.md), ordered by segment position,
then scene, then timestamp, and lays them into a handful of grid layouts
(1-up / 2-up / 3-up). **Placement heuristic:** within each (segment, scene)
group, photos are chunked into pages by cycling through layout capacities
``[3, 2, 1]`` (3-up, then 2-up, then 1-up, repeating) — this both varies the
page rhythm visually and, by trying the densest layout first each cycle,
keeps the page count down for a large scene while still giving small scenes
(1-2 photos) a spacious full/half-page treatment on the cycle's later slots.
Each segment's first page (when segments exist) is a section-header page
carrying the segment's ``label``/``place``; **an album with zero segments
skips section pages entirely** and treats all its selected photos as one
implicit group — the genericity fallback for this stage.

Trim size, bleed, and spine width come from Lulu's print spec, resolved as
[open question 4](/build/open-questions.md): Square 8.5x8.5in trim, 0.125in bleed on
every edge (rendered page = trim + 2*bleed), spine width =
``page_count / 444 + 0.06in`` (444 = Lulu's PPI constant for standard
softcover perfect-bound paper stock). The spine width is computed and returned
for the cover (a future step's concern — `assemble` itself only renders
interior pages) but not laid into the interior PDF, which has no spine.

Idempotent: skips regeneration if ``Album.assembled_pdf_path`` is already set,
unless ``force=True``. Writes the PDF to
``<output_dir>/<slug>/assembled.pdf`` and stores that path on the ``Album``
row — the integration contract [`fulfill`](/stages/fulfill.md) (built in
parallel) reads to submit the print order. No Prefect imports
(folder-structure.md rule). :class:`AssembleStage`'s ``run`` takes ``album_id`` first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import Session
from weasyprint import HTML

from album.db.models import Album, Photo, Segment

_log = logging.getLogger(__name__)

# --- Lulu print spec (open question 4, resolved step 13) -------------------
# Square 8.5x8.5in trim is a standard Lulu photo-book size. Bleed and PPI are
# from Lulu's Print API docs (Book Creation Guide / PDF Creation Settings /
# "How is spine width calculated?").
TRIM_WIDTH_IN = 8.5
TRIM_HEIGHT_IN = 8.5
BLEED_IN = 0.125
SAFETY_MARGIN_IN = 0.5
# Softcover perfect-bound spine width: page_count / PPI + cover material offset.
# PPI = pages that pack into one inch of Lulu's standard interior paper stock.
SPINE_PPI = 444.0
SPINE_BASE_IN = 0.06

PAGE_WIDTH_IN = TRIM_WIDTH_IN + 2 * BLEED_IN
PAGE_HEIGHT_IN = TRIM_HEIGHT_IN + 2 * BLEED_IN

# Placement heuristic: cycle through these layout capacities per (segment,
# scene) group — densest first, so large scenes stay page-efficient while
# small ones still land on a spacious layout. See module docstring.
_LAYOUT_CYCLE: tuple[tuple[int, str], ...] = (
    (3, "layout_3up.html"),
    (2, "layout_2up.html"),
    (1, "layout_1up.html"),
)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"


@dataclass
class AssembleResult:
    pages: int = 0
    photos_placed: int = 0
    spine_width_in: float = 0.0
    output_path: str | None = None
    skipped: bool = False


def spine_width_in(page_count: int) -> float:
    """Lulu's softcover perfect-bound spine width formula (open question 4)."""
    return page_count / SPINE_PPI + SPINE_BASE_IN


class AssembleStage:
    """`assemble` — lay out selected photos into page templates and render a
    print-ready PDF via WeasyPrint. Idempotent.

    No injected external boundary (it drives Jinja2/WeasyPrint directly); it's a
    class because it owns several layout helpers plus the ``output_dir`` /
    ``templates_dir`` configuration. ``run`` takes ``album_id`` first.
    """

    def __init__(self, output_dir: str | Path, *, templates_dir: Path = _TEMPLATES_DIR) -> None:
        self._output_dir = output_dir
        self._templates_dir = templates_dir

    def run(self, album_id: int, session: Session, *, force: bool = False) -> AssembleResult:
        """Lay out ``album_id``'s selected photos and render a print PDF. Idempotent."""
        album = session.get(Album, album_id)
        if album is None:
            raise ValueError(f"no album with id {album_id}")

        if album.assembled_pdf_path is not None and not force:
            return AssembleResult(output_path=album.assembled_pdf_path, skipped=True)

        all_photos = list(session.scalars(select(Photo).where(Photo.album_id == album_id)))
        # Reset so a re-run is deterministic and never leaves a stale page_number on
        # a photo that was placed by a previous run but is no longer selected
        # (mirrors cluster.py/select.py's reset-up-front idempotency pattern).
        for photo in all_photos:
            photo.page_number = None

        photos = [p for p in all_photos if p.selected]
        missing_render = [p for p in photos if p.rendered_path is None]
        if missing_render:
            raise ValueError(
                f"album {album_id}: {len(missing_render)} selected photo(s) have no "
                "rendered_path yet — run `render` before `assemble`"
            )

        html_pages, page_number, photos_placed = self._lay_out(album, photos)

        output_path = Path(self._output_dir) / album.slug / "assembled.pdf"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._render_pdf(html_pages, output_path)

        album.assembled_pdf_path = str(output_path)
        session.flush()

        return AssembleResult(
            pages=page_number,
            photos_placed=photos_placed,
            spine_width_in=spine_width_in(page_number),
            output_path=str(output_path),
        )

    def _lay_out(self, album: Album, photos: list[Photo]) -> tuple[list[str], int, int]:
        """Render each (segment, scene) group to page HTML, stamping each placed
        photo's ``page_number``. Returns ``(html_pages, page_count, photos_placed)``."""
        env = Environment(loader=FileSystemLoader(self._templates_dir), autoescape=True)
        base_ctx = {
            "album_title": album.title,
            "page_width_in": PAGE_WIDTH_IN,
            "page_height_in": PAGE_HEIGHT_IN,
            "bleed_in": BLEED_IN,
            "safety_margin_in": SAFETY_MARGIN_IN,
        }

        page_number = 0
        photos_placed = 0
        html_pages: list[str] = []
        for segment, group in self._group_photos(album, photos):
            if segment is not None:
                page_number += 1
                html_pages.append(
                    env.get_template("section.html").render(
                        **base_ctx, label=segment.label, place=segment.place
                    )
                )
            for chunk in self._chunk_by_scene(group):
                page_number += 1
                html_pages.append(
                    env.get_template(self._layout_for_size(len(chunk))).render(
                        **base_ctx, photos=[self._photo_ctx(p) for p in chunk]
                    )
                )
                for photo in chunk:
                    photo.page_number = page_number
                    photos_placed += 1
        return html_pages, page_number, photos_placed

    def _photo_ctx(self, photo: Photo) -> dict[str, Any]:
        return {"image_path": photo.rendered_path, "caption": photo.caption}

    def _group_photos(
        self, album: Album, photos: list[Photo]
    ) -> list[tuple[Segment | None, list[Photo]]]:
        """Order photos by segment position, then scene, then timestamp, and group
        them by segment — each group is (segment_or_None, photos). No segments
        (or none of the selected photos have one) -> a single implicit group with
        a ``None`` segment, so no section-header page is emitted."""
        segments_by_id = {seg.id: seg for seg in album.segments}

        def sort_key(p: Photo) -> tuple[int, int, Any]:
            segment_position = self._segment_position(segments_by_id, p.segment_id)
            scene = p.scene_id if p.scene_id is not None else 0
            # Undated photos sort after dated ones within their scene, deterministically.
            return (segment_position, scene, (p.timestamp is None, p.timestamp, p.id))

        ordered = sorted(photos, key=sort_key)

        groups: list[tuple[Segment | None, list[Photo]]] = []
        for segment_id, group_iter in groupby(ordered, key=lambda p: p.segment_id):
            segment = segments_by_id.get(segment_id) if segment_id is not None else None
            groups.append((segment, list(group_iter)))
        return groups

    def _segment_position(self, segments_by_id: dict[int, Segment], segment_id: int | None) -> int:
        if segment_id is None:
            return 0
        segment = segments_by_id.get(segment_id)
        # orphaned/out-of-range segment_id: sort with the unsegmented group
        return segment.position if segment is not None else 0

    def _chunk_by_scene(self, photos: list[Photo]) -> list[list[Photo]]:
        """Split one segment's (already sorted) photos into page-sized chunks,
        per-scene, cycling through the layout capacities in ``_LAYOUT_CYCLE``."""
        chunks: list[list[Photo]] = []
        for _scene_id, group_iter in groupby(photos, key=lambda p: p.scene_id):
            scene_photos = list(group_iter)
            cycle_pos = 0
            i = 0
            while i < len(scene_photos):
                capacity, _ = _LAYOUT_CYCLE[cycle_pos % len(_LAYOUT_CYCLE)]
                chunks.append(scene_photos[i : i + capacity])
                i += capacity
                cycle_pos += 1
        return chunks

    def _layout_for_size(self, size: int) -> str:
        """``_chunk_by_scene`` only ever slices chunks to one of ``_LAYOUT_CYCLE``'s
        capacities, so every real chunk size has an exact match here — a mismatch
        means the chunker's invariant broke, which should fail loudly rather than
        silently fall back to a guessed layout."""
        for capacity, template_name in _LAYOUT_CYCLE:
            if size == capacity:
                return template_name
        raise ValueError(f"no layout template for a chunk of size {size}")

    def _render_pdf(self, html_pages: list[str], output_path: Path) -> None:
        # base_url="." resolves each page's <img src> (Photo.rendered_path) against
        # the process's working directory, matching how that path is stored — not
        # the templates dir, which holds no photos.
        if not html_pages:
            empty_doc = (
                f"<html><head><style>@page {{ size: {PAGE_WIDTH_IN}in {PAGE_HEIGHT_IN}in; "
                "margin: 0; }}</style></head><body></body></html>"
            )
            HTML(string=empty_doc, base_url=".").write_pdf(str(output_path))
            return
        documents = [HTML(string=html, base_url=".").render() for html in html_pages]
        all_pages = [page for doc in documents for page in doc.pages]
        # weasyprint's inferred stub types `pages` as `str` (from its `='all'`
        # default) even though `Document.copy` actually accepts any iterable of
        # `Page` objects (see its docstring) — a third-party stub gap, not a real
        # type error.
        documents[0].copy(all_pages).write_pdf(str(output_path))  # pyright: ignore[reportArgumentType]
