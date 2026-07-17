"""``PhotoAnalyzer`` — the shared per-photo analysis collaborator.

Per [architecture.md](/foundations/architecture.md), ``enhance`` and ``caption`` both need
one downscaled thumbnail of each selected photo, and fetching it is the expensive
part — so a single collaborator produces *both* outputs from one fetch and both
stages route through it. Step 29 split the work behind that fetch into two injectable
collaborators (local-analysis.md): a :class:`~album.analysis.tone_estimator.ToneEstimator`
for the bounded tone deltas ``enhance`` applies, and a
:class:`~album.analysis.captioner.Captioner` for the caption. Both default to the
fully-offline classical implementations; the captioner is swappable per run (a
``ClaudeCaptioner`` for richer captions — quality-practices.md §1.4/§1.6.2).

The "analyzed at most once per photo" contract is enforced against the **DB** (the
five stored fields), not a shared in-memory instance, so ``enhance``'s analyzer and
``caption``'s analyzer (two separate instances) still never analyze a photo twice.
This module owns the DB side (``Session`` + ``Photo`` writes); the tone/caption work
itself is pure and lives in the [`analysis`](../analysis) package.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from album.analysis.captioner import Captioner, TemplateCaptioner
from album.analysis.context import PhotoContext
from album.analysis.tone import ToneParams
from album.analysis.tone_estimator import ClassicalToneEstimator, ToneEstimator
from album.db.models import Photo


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
    leaks into the caption. A photo with no assigned people yields ``[]``, leaving
    the caption identical to a face-free album's (genericity guard)."""
    if not photo.person_ids:
        return []
    names_by_id = {person.immich_person_id: person.name for person in photo.album.people}
    return [name for pid in photo.person_ids if (name := names_by_id.get(pid)) is not None]


def _format_place(photo: Photo) -> str | None:
    """The photo's reverse-geocoded place as one line — the caption's location
    grounding (enrichment step 20).

    Joins Immich's ``city``/``state``/``country`` (most-to-least specific),
    dropping any that are unset and de-duplicating repeats so a city that equals
    its state doesn't read as "Singapore, Singapore". ``None`` when the photo has
    no geocoded place at all (no GPS, or Immich didn't geocode it), leaving the
    caption unchanged (genericity guard)."""
    # dict.fromkeys → unique, order-preserving (same dedup idiom as sync's
    # person_ids); the trailing `or None` turns the all-empty case into None.
    parts = dict.fromkeys(part for part in (photo.city, photo.state, photo.country) if part)
    return ", ".join(parts) or None


class PhotoAnalyzer:
    """Runs (and persists) the shared analysis for a photo, exactly once.

    Both collaborators default to the fully-offline classical implementations
    (``ClassicalToneEstimator`` + ``TemplateCaptioner``), so a default pipeline run
    needs no external API and no API key. Inject a ``ClaudeCaptioner`` (via the
    ``captioner`` argument) for the opt-in rich-caption mode.
    """

    def __init__(
        self,
        thumbnails: ThumbnailSource,
        *,
        tone_estimator: ToneEstimator | None = None,
        captioner: Captioner | None = None,
    ) -> None:
        self._thumbnails = thumbnails
        self._tone = tone_estimator if tone_estimator is not None else ClassicalToneEstimator()
        self._captioner = captioner if captioner is not None else TemplateCaptioner()

    def ensure(
        self,
        session: Session,
        photo: Photo,
        *,
        force: bool = False,
    ) -> ToneParams | None:
        """Idempotent trigger for the shared per-photo analysis.

        The **single place** that estimates a photo's tone + caption. Both
        ``EnhanceStage`` and ``CaptionStage`` route through an injected analyzer, so
        a photo is analyzed at most once.

        Contract:
        - If ``photo`` already has all five stored fields populated
          (``enhance_exposure_ev``, ``enhance_temp_shift_k``, ``enhance_shadows``,
          ``enhance_highlights``, ``caption``) and ``force`` is ``False``, this is a
          no-op: returns ``None`` and does **not** fetch a thumbnail or touch the row.
        - Otherwise: fetches the thumbnail once, builds a ``PhotoContext``, runs the
          tone estimator and the captioner over it, writes all five fields onto
          ``photo`` in one shot, and returns the fresh **raw** tone delta.

        The return value is the *raw* tone delta (what the estimator produced),
        which ``EnhanceStage`` sums onto its per-camera baseline. ``force`` matters
        because ``EnhanceStage`` overwrites the ``enhance_*`` columns with the
        *reconciled* (baseline + delta) values, not the raw delta — so a re-run must
        re-estimate rather than read the (now-reconciled) columns; ``force`` is what
        makes that possible.

        Does not ``flush``/``commit`` — the caller controls the transaction boundary.
        """
        if self._is_already_analyzed(photo) and not force:
            return None

        image = self._thumbnails.get_thumbnail(photo.immich_asset_id)
        context = self._build_context(photo)
        tone = self._tone.estimate(image)
        caption = self._captioner.caption(image, context)
        self._persist(photo, tone, caption)
        return tone

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
        """The ``PhotoContext`` for ``photo``: segment label/place (both optional —
        an unsegmented album or out-of-range photo gets neither, never an error),
        ``Album.meta``, the photo's named people, its reverse-geocoded place, and its
        capture time (all optional; each absent one simply adds no grounding)."""
        return PhotoContext(
            segment_label=photo.segment.label if photo.segment is not None else None,
            segment_place=photo.segment.place if photo.segment is not None else None,
            album_meta=photo.album.meta,
            person_names=_resolve_person_names(photo),
            photo_place=_format_place(photo),
            capture_time=photo.timestamp,
        )

    @staticmethod
    def _persist(photo: Photo, tone: ToneParams, caption: str) -> None:
        # caption is written here, not by ToneParams.write_to — it is not a tone
        # knob, so the value object deliberately excludes it.
        tone.write_to(photo)
        photo.caption = caption
