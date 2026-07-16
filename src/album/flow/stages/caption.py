"""``caption`` — guarantee every selected photo has a stored caption, scoped
by ``album_id``.

Per [caption.md](/stages/caption.md): the caption is produced by the **shared
analysis call** described under [`enhance`](./enhance.py) (one agent, one
round-trip per selected photo, producing tone deltas and a caption together).
If `enhance` already ran for a photo, the caption is simply read back; if
`caption` runs first, it triggers that same idempotent call. Either way there
is **exactly one API round-trip per photo** across the whole pipeline — this
stage does not reimplement that idempotency, it routes through the injected
:class:`~album.flow.photo_analyzer.PhotoAnalyzer`'s ``ensure``, the single shared
trigger point both stages use (see its docstring for the exact no-op contract).

This stage's own job is narrow: iterate the album's ``selected`` photos, call
``analyzer.ensure`` for each (passing ``force`` through), and count outcomes.
Context grounding (segment label/place, ``Album.meta``) is assembled inside
``PhotoAnalyzer.ensure``/``analyze_photo`` — a segment-less album or an
out-of-segment photo still gets an accurate, if generic, caption rather than
erroring (AGENTS.md rule 1 — genericity).

No Prefect imports (folder-structure.md rule). :class:`CaptionStage`'s ``run``
takes ``album_id`` first. Per-photo failures are isolated (try/except + log +
continue), consistent with ``quality.py``/``enhance.py``, so one bad photo
(unreadable thumbnail, unparseable model response) doesn't sink an album's
captioning run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.flow.photo_analyzer import PhotoAnalyzer
from album.flow.stages.base import Stage

_log = logging.getLogger(__name__)


@dataclass
class CaptionResult:
    captioned: int = 0
    skipped: int = 0
    failed: int = 0


class CaptionStage(Stage[CaptionResult]):
    """`caption` — guarantee every selected photo has a stored caption. Idempotent.

    Owns no vision logic of its own: it routes through the same injected
    :class:`~album.flow.photo_analyzer.PhotoAnalyzer` that ``enhance`` uses, so the
    caption is produced by the one shared call (exactly one API round-trip per
    photo across the whole pipeline, regardless of stage order).
    """

    def __init__(self, analyzer: PhotoAnalyzer) -> None:
        self._analyzer = analyzer

    def _process(self, album: Album, session: Session, *, force: bool) -> CaptionResult:
        """Ensure every ``selected`` photo in ``album`` has a stored caption.

        Idempotent unless ``force``: a photo whose shared analysis already ran
        (via a prior `enhance` run, or a prior `caption` run) is not re-analyzed —
        the analyzer returns ``None`` and no API call is made. Order between
        `enhance` and `caption` does not matter; whichever runs first triggers the
        one shared call.
        """
        result = CaptionResult()
        photos = session.scalars(
            select(Photo).where(Photo.album_id == album.id, Photo.selected == True)  # noqa: E712
        )
        for photo in photos:
            self._caption_photo(photo, session, force, result)

        session.flush()
        return result

    def _caption_photo(
        self, photo: Photo, session: Session, force: bool, result: CaptionResult
    ) -> None:
        """Ensure one photo's shared analysis has run, tallying the outcome onto
        ``result``. Isolates a per-photo failure so one bad photo (unreadable
        thumbnail, unparseable model response) doesn't sink the whole album's
        captioning run."""
        try:
            analysis = self._analyzer.ensure(session, photo, force=force)
        except Exception:
            _log.warning("failed to caption photo %s (%s)", photo.id, photo.filename, exc_info=True)
            result.failed += 1
            return

        if analysis is None:
            result.skipped += 1
        else:
            result.captioned += 1


__all__ = ["CaptionResult", "CaptionStage"]
