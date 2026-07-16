"""``PhotoAnalyzer`` — the shared "one combined vision call" collaborator.

Per [architecture.md](/foundations/architecture.md)'s "One combined vision call, not two":
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
from album.tone import ToneParams


class ThumbnailSource(Protocol):
    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes: ...


def _resolve_person_names(photo: Photo) -> list[str]:
    """The *named* people in ``photo``, in ``person_ids`` order — the caption's
    name grounding (enrichment step 19).

    Resolves the photo's assigned Immich person ids to display names via the
    album-scoped ``Person`` cache (``photo.album.people`` — one query per album,
    reused from the session's identity map, so no per-photo round trip). A person
    unnamed in Immich (``Person.name is None``) or with no cached ``Person`` row
    contributes no name, so a fabricated name is never passed and no ``None``
    leaks into the prompt. A photo with no assigned people yields ``[]``, leaving
    the prompt identical to a face-free album's (genericity guard)."""
    if not photo.person_ids:
        return []
    names_by_id = {person.immich_person_id: person.name for person in photo.album.people}
    return [name for pid in photo.person_ids if (name := names_by_id.get(pid)) is not None]


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
          error), ``Album.meta``, and the names of the photo's recognized people
          (:func:`_resolve_person_names`, empty when the album has no named
          faces), calls ``analyze_photo``, writes all five fields onto ``photo``
          in one shot, and returns the fresh :class:`AnalysisResult`.

        ``force`` matters because ``EnhanceStage`` overwrites ``enhance_exposure_ev``
        et al. with the *final, reconciled* (baseline + AI delta) values, not the
        raw AI delta — so once a photo has been enhanced once, those columns no
        longer hold a value ``reconcile_tone`` can be fed a second time without
        double-applying the baseline. A ``force=True`` re-run therefore must
        re-fetch the raw delta from a fresh analysis call rather than reading the
        (now-reconciled) stored columns — this flag is what makes that possible.

        Does not ``flush``/``commit`` — the caller controls the transaction boundary.
        """
        if self._is_already_analyzed(photo) and not force:
            return None

        image = self._thumbnails.get_thumbnail(photo.immich_asset_id)
        context = self._build_context(photo)
        result = analyze_photo(image, context, model=self._model)
        self._persist(photo, result)
        return result

    @staticmethod
    def _is_already_analyzed(photo: Photo) -> bool:
        """Whether all five stored fields are already populated — the no-op
        half of ``ensure``'s idempotency contract."""
        return (
            photo.enhance_exposure_ev is not None
            and photo.enhance_temp_shift_k is not None
            and photo.enhance_shadows is not None
            and photo.enhance_highlights is not None
            and photo.caption is not None
        )

    @staticmethod
    def _build_context(photo: Photo) -> PhotoContext:
        """The ``PhotoContext`` for ``photo``'s vision call: segment label/place
        (both optional — an unsegmented album or out-of-range photo gets neither,
        never an error), ``Album.meta``, and the photo's named people."""
        return PhotoContext(
            segment_label=photo.segment.label if photo.segment is not None else None,
            segment_place=photo.segment.place if photo.segment is not None else None,
            album_meta=photo.album.meta,
            person_names=_resolve_person_names(photo),
        )

    @staticmethod
    def _persist(photo: Photo, result: AnalysisResult) -> None:
        # caption is written here, not by ToneParams.write_to — it is not a tone
        # knob, so the value object deliberately excludes it.
        ToneParams.from_analysis(result).write_to(photo)
        photo.caption = result.caption
