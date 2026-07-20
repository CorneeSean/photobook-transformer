"""``caption`` — guarantee every selected photo has a stored caption, scoped
by ``album_id``.

Per [caption.md](/stages/caption.md): the caption is produced by the **shared
per-photo analysis** described under [`enhance`](./enhance.py) (one thumbnail fetch
per selected photo, producing tone deltas and a caption together; both are classical
and offline by default — step 29/local-analysis.md). If `enhance` already ran for a
photo, the caption is simply read back; if `caption` runs first, it triggers that
same idempotent call. Either way the photo is analyzed **at most once** across the
whole pipeline — this stage does not reimplement that idempotency, it routes through
the injected :class:`~album.flow.photo_analyzer.PhotoAnalyzer`'s ``ensure``, the single
shared trigger point both stages use (see its docstring for the exact no-op contract).

This stage's own job is narrow: iterate the album's ``selected`` photos, call
``analyzer.ensure`` for each (passing ``force`` through), and count outcomes.
Context grounding (segment label/place, ``Album.meta``, people, place, time) is
assembled inside ``PhotoAnalyzer.ensure`` — a segment-less album or an out-of-segment
photo still gets an accurate, if generic, caption rather than erroring (AGENTS.md
rule 1 — genericity).

:class:`CaptionStage` is a :class:`~album.flow.stages.base.Stage` (folder-structure.md
rule) — it takes its ``album_id`` + ``session_factory`` at construction, opens the
session inside ``run``, and is unit-testable without an orchestrator; it runs as a
Prefect task via the ``run_as_task`` / ``to_task`` glue inherited from ``TaskUnit``
(quality-practices.md §1.5). Per-photo failures are isolated
(try/except + log + continue), consistent with ``quality.py``/``enhance.py``, so
one bad photo (unreadable thumbnail, unparseable model response) doesn't sink an
album's captioning run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy.orm import Session

from album.db.models import Photo
from album.db.queries import selected_photos
from album.db.session import SessionFactory
from album.flow.photo_analyzer import PhotoAnalyzer
from album.flow.stages._progress import ProgressLogger
from album.flow.stages.base import Stage

_log = logging.getLogger(__name__)


@dataclass
class CaptionResult:
    captioned: int = 0
    skipped: int = 0
    failed: int = 0


class CaptionStage(Stage[CaptionResult]):
    """`caption` — guarantee every selected photo has a stored caption. Idempotent.

    Owns no caption logic of its own: it routes through the same injected
    :class:`~album.flow.photo_analyzer.PhotoAnalyzer` that ``enhance`` uses, so the
    caption is produced by the one shared analysis (the photo is analyzed at most
    once across the whole pipeline, regardless of stage order — and, by default,
    fully offline with no API call).
    """

    task_name: ClassVar[str] = "caption"

    def __init__(
        self,
        analyzer: PhotoAnalyzer,
        album_id: int,
        session_factory: SessionFactory,
        *,
        force: bool = False,
    ) -> None:
        super().__init__(album_id, session_factory, force=force)
        self._analyzer = analyzer

    def _process(self) -> CaptionResult:
        """Ensure every ``selected`` photo in ``self._album`` has a stored caption.

        Idempotent unless ``force``: a photo whose shared analysis already ran
        (via a prior `enhance` run, or a prior `caption` run) is not re-analyzed —
        the analyzer returns ``None`` and no API call is made. Order between
        `enhance` and `caption` does not matter; whichever runs first triggers the
        one shared call.
        """
        result = CaptionResult()
        photos = selected_photos(self._album.id, self._session)
        label = f"caption album={self._album.slug}"
        with ProgressLogger(_log, label, total=len(photos)) as progress:
            for photo in photos:
                self._caption_photo(photo, self._session, self._force, result)
                progress.tick()

        return result

    def _failure_counts(self, result: CaptionResult) -> tuple[int, int]:
        # Unlike the other stages, caption's `skipped` is a success *elsewhere*, not a
        # short-circuit: in the real DAG caption runs after `enhance`, which already
        # analyzed (and captioned) every clustered photo via the shared analyzer, so
        # those come back `skipped` here. Counting them keeps a single failing
        # unclustered photo from tripping escalation on an otherwise-captioned album;
        # a true total failure (nothing captioned anywhere) still has skipped == 0.
        return result.failed, result.captioned + result.failed + result.skipped

    def _caption_photo(
        self,
        photo: Photo,
        session: Session,
        force: bool,
        result: CaptionResult,
    ) -> None:
        """Ensure one photo's shared analysis has run, tallying the outcome onto
        ``result``. Isolates a per-photo failure so one bad photo (unreadable
        thumbnail, unparseable model response) doesn't sink the whole album's
        captioning run."""
        try:
            outcome = self._analyzer.ensure(session, photo, force=force)
        except Exception:
            _log.warning("failed to caption photo %s (%s)", photo.id, photo.filename, exc_info=True)
            result.failed += 1
            return

        if outcome is None:
            result.skipped += 1
        else:
            result.captioned += 1


__all__ = ["CaptionResult", "CaptionStage"]
