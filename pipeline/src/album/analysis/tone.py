"""``ToneParams`` — the four bounded tone knobs as a standalone value object,
plus the bounds they must stay within and the conversion to/from the
:class:`~album.db.models.Photo` ORM row (the same four columns, ``enhance_``-prefixed).

Centralizing the column mapping here is what lets ``enhance`` and ``photo_analyzer``
share one field mapping instead of hand-copying the four knobs at each boundary. It
lives in the ``analysis`` package (which ``flow`` depends on, not vice versa) so the
classical tone estimator can import it without dragging in the whole ``flow`` package
— importing ``album.flow.*`` runs ``flow/__init__`` and the pipeline wiring, which
would form an import cycle back through ``photo_analyzer``. The bound constants are
the single source of truth for "how large a
tone correction is allowed" (AGENTS.md rule 5 — derived corrections are bounded,
never drastic): the classical tone estimator clamps its estimates to them, and
``enhance``'s :func:`~album.flow.stages.enhance.reconcile_tone` clamps the
baseline-plus-delta sum to them.
"""

from __future__ import annotations

from dataclasses import dataclass

from album.db.models import Photo

# The bounds every tone knob is held within, mirrored by the ``Photo.enhance_*``
# columns these are written to (data-model.md). One authority for both the
# estimator and enhance's reconciliation.
EXPOSURE_EV_BOUND = 2.0
TEMP_SHIFT_K_BOUND = 2000.0
SHADOWS_BOUND = 1.0
HIGHLIGHTS_BOUND = 1.0


@dataclass(frozen=True)
class ToneParams:
    """The four bounded tone knobs, freestanding — used for the baseline offset,
    the classical per-photo delta, and their reconciled sum alike, so
    :func:`~album.flow.stages.enhance.reconcile_tone` can be pure arithmetic over one
    type instead of juggling three."""

    exposure_ev: float = 0.0
    temp_shift_k: float = 0.0
    shadows: float = 0.0
    highlights: float = 0.0

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


def clamp(value: float, bound: float) -> float:
    """Clamp ``value`` to the symmetric range ``[-bound, bound]`` — the one place
    both the classical tone estimator and ``enhance``'s reconciliation hold a knob
    to its bound."""
    return max(-bound, min(bound, value))


__all__ = [
    "EXPOSURE_EV_BOUND",
    "HIGHLIGHTS_BOUND",
    "SHADOWS_BOUND",
    "TEMP_SHIFT_K_BOUND",
    "ToneParams",
    "clamp",
]
