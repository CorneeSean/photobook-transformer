"""``PhotoAnalyzer`` — the shared "one combined vision call" collaborator.

Per [architecture.md](/architecture.md)'s "One combined vision call, not two":
[`enhance`](stages/enhance.py) and [`caption`](stages/caption.py) both need the
same single agent call per selected photo (bounded tone deltas *and* a caption
together, because uploading the thumbnail is the expensive part). Rather than one
stage importing a function from the other, that shared trigger is its own injected
object — a :class:`PhotoAnalyzer` holding the :class:`ThumbnailSource` and the
vision ``model`` — which **both** ``EnhanceStage`` and ``CaptionStage`` receive in
their constructors (quality-practices.md §1.4).

The "exactly one round-trip per photo" contract is enforced against the **DB**
(the five stored fields), not against a shared in-memory instance, so two separate
``PhotoAnalyzer`` instances (enhance's and caption's) still never analyze a photo
twice. This module owns the DB side (``Session`` + ``Photo`` writes); the pure,
side-effect-free vision call itself stays in [`agents/analysis.py`](agents/analysis.py).
"""

from __future__ import annotations

from typing import Protocol

from pydantic_ai.models import Model
from sqlalchemy.orm import Session

from album.agents.analysis import AnalysisResult, PhotoContext, analyze_photo
from album.db.models import Photo


class ThumbnailSource(Protocol):
    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes: ...


class PhotoAnalyzer:
    """Runs (and persists) the shared vision call for a photo, exactly once.

    ``model`` defaults to real Claude (built inside ``analyze_photo`` from
    ``config.py``); tests inject PydanticAI's ``TestModel``/``FunctionModel``.
    """

    def __init__(self, thumbnails: ThumbnailSource, *, model: Model | None = None) -> None:
        self._thumbnails = thumbnails
        self._model = model

    def ensure(
        self, session: Session, photo: Photo, *, force: bool = False
    ) -> AnalysisResult | None:
        """Idempotent trigger for the one-combined-vision-call design.

        The **single place** that calls :func:`album.agents.analysis.analyze_photo`
        for a given photo. Both ``EnhanceStage`` and ``CaptionStage`` route through
        an injected analyzer, so a photo is never analyzed twice.

        Contract:
        - If ``photo`` already has all five stored fields populated
          (``enhance_exposure_ev``, ``enhance_temp_shift_k``, ``enhance_shadows``,
          ``enhance_highlights``, ``caption``) and ``force`` is ``False``, this is
          a no-op: returns ``None`` and does **not** touch the network or the row.
        - Otherwise: fetches a thumbnail, builds a ``PhotoContext`` from the
          photo's segment (label/place, both optional — an unsegmented album or an
          out-of-range photo simply gets a context with neither set, never an
          error) and ``Album.meta``, calls ``analyze_photo``, writes all five
          fields onto ``photo`` in one shot, and returns the fresh
          :class:`AnalysisResult`.

        ``force`` matters because ``EnhanceStage`` overwrites ``enhance_exposure_ev``
        et al. with the *final, reconciled* (baseline + AI delta) values, not the
        raw AI delta — so once a photo has been enhanced once, those columns no
        longer hold a value ``reconcile_tone`` can be fed a second time without
        double-applying the baseline. A ``force=True`` re-run therefore must
        re-fetch the raw delta from a fresh analysis call rather than reading the
        (now-reconciled) stored columns — this flag is what makes that possible.

        Does not ``flush``/``commit`` — the caller controls the transaction boundary.
        """
        already_analyzed = (
            photo.enhance_exposure_ev is not None
            and photo.enhance_temp_shift_k is not None
            and photo.enhance_shadows is not None
            and photo.enhance_highlights is not None
            and photo.caption is not None
        )
        if already_analyzed and not force:
            return None

        image = self._thumbnails.get_thumbnail(photo.immich_asset_id)
        context = PhotoContext(
            segment_label=photo.segment.label if photo.segment is not None else None,
            segment_place=photo.segment.place if photo.segment is not None else None,
            album_meta=photo.album.meta,
        )
        result = analyze_photo(image, context, model=self._model)

        photo.enhance_exposure_ev = result.exposure_ev
        photo.enhance_temp_shift_k = result.temp_shift_k
        photo.enhance_shadows = result.shadows
        photo.enhance_highlights = result.highlights
        photo.caption = result.caption
        return result
