"""``ToneParams`` — the four bounded tone knobs as a standalone value object,
plus the conversions between it and the two representations that embed the same
knobs under different names: the AI's :class:`~album.agents.analysis.AnalysisResult`
(bare names, plus a caption) and the :class:`~album.db.models.Photo` ORM row
(the same four columns, ``enhance_``-prefixed).

Centralizing those conversions here is what lets ``enhance`` and
``photo_analyzer`` share one field mapping instead of hand-copying the four knobs
at each boundary. Because this module imports neither of those two, it also
breaks the ``enhance`` → ``photo_analyzer`` import cycle that previously forced
the copies to be duplicated (``ToneParams`` used to live in ``enhance``, which
``photo_analyzer`` cannot import back).
"""

from __future__ import annotations

from dataclasses import dataclass

from album.agents.analysis import AnalysisResult
from album.db.models import Photo


@dataclass(frozen=True)
class ToneParams:
    """The four bounded tone knobs, freestanding — used for the baseline offset,
    the AI delta, and their reconciled sum alike, so
    :func:`~album.flow.stages.enhance.reconcile_tone` can be pure arithmetic over one
    type instead of juggling three."""

    exposure_ev: float = 0.0
    temp_shift_k: float = 0.0
    shadows: float = 0.0
    highlights: float = 0.0

    @classmethod
    def from_analysis(cls, analysis: AnalysisResult) -> ToneParams:
        """The raw per-photo tone delta from a fresh vision call (the caption,
        the fifth ``AnalysisResult`` field, is not a tone knob and is dropped)."""
        return cls(
            exposure_ev=analysis.exposure_ev,
            temp_shift_k=analysis.temp_shift_k,
            shadows=analysis.shadows,
            highlights=analysis.highlights,
        )

    @classmethod
    def from_photo(cls, photo: Photo) -> ToneParams:
        """The tone knobs stored on ``photo``'s ``enhance_*`` columns, coalescing
        an unset (``None``) column to ``0.0`` so the result is always usable
        arithmetic."""
        return cls(
            exposure_ev=photo.enhance_exposure_ev or 0.0,
            temp_shift_k=photo.enhance_temp_shift_k or 0.0,
            shadows=photo.enhance_shadows or 0.0,
            highlights=photo.enhance_highlights or 0.0,
        )

    def write_to(self, photo: Photo) -> None:
        """Write these four knobs onto ``photo``'s ``enhance_*`` columns. Does not
        touch ``caption`` (not a tone knob) and does not flush/commit — the caller
        owns the transaction boundary."""
        photo.enhance_exposure_ev = self.exposure_ev
        photo.enhance_temp_shift_k = self.temp_shift_k
        photo.enhance_shadows = self.shadows
        photo.enhance_highlights = self.highlights


__all__ = ["ToneParams"]
