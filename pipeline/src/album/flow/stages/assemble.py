"""``assemble`` — lay out selected photos into the book's page order, scoped by
``album_id``.

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

This stage produces **only the layout**: it stamps each placed photo's
``page_number`` (the reader [`printers.base.iter_book_pages`](../../printers/base.py)
reconstructs the book from those stamps) and returns the page count + Lulu spine
width. It renders **no** file — turning the layout into a concrete medium (a
browsable webpage, a print-ready PDF) is a [`Printer`](../../printers/base.py)'s
job, run on demand by [`fulfill`](fulfill.py). In particular the master PDF is now
rendered inside :class:`~album.printers.lulu.LuluPrinter` only when a physical
order is actually requested, rather than unconditionally here on every run.

Spine width comes from Lulu's print spec, resolved as
[open question 4](/build/open-questions.md): spine width =
``page_count / 444 + 0.06in`` (444 = Lulu's PPI constant for standard
softcover perfect-bound paper stock). It is computed and returned for the cover
(a future step's concern) but the interior has no spine.

Idempotent: skips re-layout if the album's photos are already placed (any photo
carries a ``page_number``), unless ``force=True``. :class:`AssembleStage` is a
:class:`~album.flow.stages.base.Stage` (folder-structure.md rule) — it takes its
``album_id`` + ``session_factory`` at construction, opens the session inside
``run``, and is unit-testable without an orchestrator; it runs as a Prefect task via
the ``run_as_task`` / ``to_task`` glue inherited from ``TaskUnit``
(quality-practices.md §1.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import Any, ClassVar

from sqlalchemy import select

from album.db.models import Album, Photo, Segment
from album.flow.stages.base import Stage

# --- Lulu spine spec (open question 4, resolved step 13) -------------------
# Softcover perfect-bound spine width: page_count / PPI + cover material offset.
# PPI = pages that pack into one inch of Lulu's standard interior paper stock.
# (Page geometry — trim, bleed, page box — lives with PDF rendering in
# printers/lulu.py, the only consumer that needs it.)
SPINE_PPI = 444.0
SPINE_BASE_IN = 0.06

# Placement heuristic: cycle through these per-page photo capacities per
# (segment, scene) group — densest first, so large scenes stay page-efficient
# while small ones still land on a spacious layout. See module docstring.
_LAYOUT_CAPACITIES: tuple[int, ...] = (3, 2, 1)


@dataclass
class AssembleResult:
    pages: int = 0
    photos_placed: int = 0
    spine_width_in: float = 0.0
    skipped: bool = False


def spine_width_in(page_count: int) -> float:
    """Lulu's softcover perfect-bound spine width formula (open question 4)."""
    return page_count / SPINE_PPI + SPINE_BASE_IN


class AssembleStage(Stage[AssembleResult]):
    """`assemble` — lay out selected photos into the book's page order. Idempotent.

    Stamps each placed photo's ``page_number`` and returns the page count + spine
    width; it renders no file (a :class:`~album.printers.base.Printer` does, on
    demand). No injected external boundary — it owns only the pure layout helpers
    plus the ``album_id`` + ``session_factory`` the base
    :class:`~album.flow.stages.base.Stage` takes; ``run`` takes no arguments.
    """

    task_name: ClassVar[str] = "assemble"

    def _process(self) -> AssembleResult:
        """Lay out ``self._album``'s selected photos into page order. Idempotent."""
        album, session = self._album, self._session
        all_photos = list(session.scalars(select(Photo).where(Photo.album_id == album.id)))

        placed_numbers = [p.page_number for p in all_photos if p.page_number is not None]
        if not self._force and placed_numbers:
            pages = max(placed_numbers)
            return AssembleResult(
                pages=pages,
                photos_placed=len(placed_numbers),
                spine_width_in=spine_width_in(pages),
                skipped=True,
            )

        self._reset_page_numbers(all_photos)
        photos = self._select_for_placement(all_photos, album.id)

        page_number, photos_placed = self._lay_out(album, photos)

        return AssembleResult(
            pages=page_number,
            photos_placed=photos_placed,
            spine_width_in=spine_width_in(page_number),
        )

    @staticmethod
    def _reset_page_numbers(photos: list[Photo]) -> None:
        """Reset so a re-run is deterministic and never leaves a stale page_number
        on a photo that was placed by a previous run but is no longer selected
        (mirrors cluster.py/select.py's reset-up-front idempotency pattern)."""
        for photo in photos:
            photo.page_number = None

    def _select_for_placement(self, all_photos: list[Photo], album_id: int | None) -> list[Photo]:
        """Filter to selected photos, raising if any lacks a ``rendered_path``."""
        photos = [p for p in all_photos if p.selected]
        self._require_rendered(photos, album_id)
        return photos

    @staticmethod
    def _require_rendered(photos: list[Photo], album_id: int | None) -> None:
        missing_render = [p for p in photos if p.rendered_path is None]
        if missing_render:
            raise ValueError(
                f"album {album_id}: {len(missing_render)} selected photo(s) have no "
                "rendered_path yet — run `render` before `assemble`",
            )

    def _lay_out(self, album: Album, photos: list[Photo]) -> tuple[int, int]:
        """Stamp each placed photo's ``page_number``, grouping by (segment, scene).
        Returns ``(page_count, photos_placed)`` — page count includes section pages."""
        page_number = 0
        photos_placed = 0
        for segment, group in self._group_photos(album, photos):
            page_number, group_placed = self._place_group(segment, group, page_number)
            photos_placed += group_placed
        return page_number, photos_placed

    def _place_group(
        self,
        segment: Segment | None,
        group: list[Photo],
        page_number: int,
    ) -> tuple[int, int]:
        """Advance ``page_number`` over one (segment, scene) group's section header
        (if any) and its photo pages, stamping each placed photo. Returns
        ``(page_number, photos_placed)``."""
        if segment is not None:
            page_number += 1  # section-header page
        photos_placed = 0
        for chunk in self._chunk_by_scene(group):
            page_number += 1
            for photo in chunk:
                photo.page_number = page_number
                photos_placed += 1
        return page_number, photos_placed

    def _group_photos(
        self,
        album: Album,
        photos: list[Photo],
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

    @staticmethod
    def _segment_position(segments_by_id: dict[int, Segment], segment_id: int | None) -> int:
        if segment_id is None:
            return 0
        segment = segments_by_id.get(segment_id)
        # orphaned/out-of-range segment_id: sort with the unsegmented group
        return segment.position if segment is not None else 0

    @staticmethod
    def _chunk_by_scene(photos: list[Photo]) -> list[list[Photo]]:
        """Split one segment's (already sorted) photos into page-sized chunks,
        per-scene, cycling through the layout capacities in ``_LAYOUT_CAPACITIES``."""
        chunks: list[list[Photo]] = []
        for _scene_id, group_iter in groupby(photos, key=lambda p: p.scene_id):
            scene_photos = list(group_iter)
            cycle_pos = 0
            i = 0
            while i < len(scene_photos):
                capacity = _LAYOUT_CAPACITIES[cycle_pos % len(_LAYOUT_CAPACITIES)]
                chunks.append(scene_photos[i : i + capacity])
                i += capacity
                cycle_pos += 1
        return chunks
