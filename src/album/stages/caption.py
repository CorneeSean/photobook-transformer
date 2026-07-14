"""``caption`` — guarantee every selected photo has a stored caption, scoped
by ``album_id``.

Per [caption.md](/stages/caption.md): the caption is produced by the **shared
analysis call** described under [`enhance`](./enhance.py) (one agent, one
round-trip per selected photo, producing tone deltas and a caption together).
If `enhance` already ran for a photo, the caption is simply read back; if
`caption` runs first, it triggers that same idempotent call. Either way there
is **exactly one API round-trip per photo** across the whole pipeline — this
stage does not reimplement that idempotency, it reuses
:func:`album.stages.enhance.ensure_analysis`, the single shared trigger point
both stages must route through (see its docstring for the exact no-op
contract).

This stage's own job is narrow: iterate the album's ``selected`` photos, call
``ensure_analysis`` for each (passing ``force`` through), and count outcomes.
Context grounding (segment label/place, ``Album.meta``) is assembled inside
``ensure_analysis``/``analyze_photo`` — a segment-less album or an
out-of-segment photo still gets an accurate, if generic, caption rather than
erroring (AGENTS.md rule 1 — genericity).

No Prefect imports (folder-structure.md rule). ``album_id`` is always the
first argument. Per-photo failures are isolated (try/except + log +
continue), consistent with ``quality.py``/``enhance.py``, so one bad photo
(unreadable thumbnail, unparseable model response) doesn't sink an album's
captioning run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic_ai.models import Model
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.stages.enhance import ThumbnailSource, ensure_analysis

_log = logging.getLogger(__name__)


@dataclass
class CaptionResult:
    captioned: int = 0
    skipped: int = 0
    failed: int = 0


def caption(
    album_id: int,
    session: Session,
    thumbnails: ThumbnailSource,
    model: Model | None,
    *,
    force: bool = False,
) -> CaptionResult:
    """Ensure every ``selected`` photo in ``album_id`` has a stored caption.

    Idempotent unless ``force``: a photo whose shared analysis already ran
    (via a prior `enhance` run, or a prior `caption` run) is not re-analyzed —
    ``ensure_analysis`` returns ``None`` and no API call is made. Order
    between `enhance` and `caption` does not matter; whichever runs first
    triggers the one shared call.
    """
    album = session.get(Album, album_id)
    if album is None:
        raise ValueError(f"no album with id {album_id}")

    result = CaptionResult()
    photos = session.scalars(
        select(Photo).where(Photo.album_id == album_id, Photo.selected == True)  # noqa: E712
    )
    for photo in photos:
        try:
            analysis = ensure_analysis(
                session, photo, thumbnails=thumbnails, model=model, force=force
            )
        except Exception:
            # Failure isolation: one bad photo (unreadable thumbnail,
            # unparseable model response) must not sink the whole album's
            # captioning run.
            _log.warning("failed to caption photo %s (%s)", photo.id, photo.filename, exc_info=True)
            result.failed += 1
            continue

        if analysis is None:
            result.skipped += 1
        else:
            result.captioned += 1

    session.flush()
    return result


__all__ = ["CaptionResult", "caption"]
