"""``ToneEstimator`` — bounded per-photo tone deltas, behind a substitutable seam.

``enhance`` needs four small tone-correction deltas per photo (exposure, white
balance, shadows, highlights) which classical code then applies as a darktable XMP
sidecar. Step 29 replaces the LLM that used to estimate them with deterministic,
offline computer vision — estimating tone from a 250px thumbnail was always a weak
use of an LLM (local-analysis.md). :class:`ClassicalToneEstimator` is the only
implementation; the ``ToneEstimator`` protocol exists so ``PhotoAnalyzer`` depends on
the seam (quality-practices.md §1.4/§1.6.2) and a test can substitute a stub.

Each knob is deliberately conservative and only fires on a *measured* defect, so a
well-exposed neutral frame gets ~zero correction (AGENTS.md rule 5 — bounded, never
drastic), and every estimate is clamped to the :mod:`~album.analysis.tone` bounds so
``enhance``'s reconciliation math stays valid.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from album.analysis._image import decode_color, decode_gray
from album.analysis.tone import (
    EXPOSURE_EV_BOUND,
    HIGHLIGHTS_BOUND,
    SHADOWS_BOUND,
    TEMP_SHIFT_K_BOUND,
    ToneParams,
    clamp,
)

# Auto-exposure targets a gentle mid-grey rather than a full 18% (0.18) grey card:
# a photo-book pass should nudge, not normalise every frame to the same brightness.
_TARGET_LUMA = 0.45
# Tails past these normalised luminances count as clipped-to-black / -white.
_BLACK_CLIP = 0.05
_WHITE_CLIP = 0.95
# Fraction-clipped -> shadow-lift / highlight-recovery gain (a frame half-clipped
# saturates the knob); Kelvin per unit of gray-world blue/red cast.
_SHADOW_GAIN = 2.0
_HIGHLIGHT_GAIN = 2.0
_TEMP_CAST_GAIN = 4000.0


# runtime_checkable: ``pipeline`` (@flow) takes a ``ToneEstimator`` in its signature
# and Prefect's parameter-schema build runs ``isinstance`` on each annotation — see
# the note on ``Captioner`` / ``SimilaritySearch`` (quality-practices.md §1.4).
@runtime_checkable
class ToneEstimator(Protocol):
    def estimate(self, image: bytes) -> ToneParams:
        """Bounded per-photo tone deltas for one thumbnail — a *raw* delta
        ``enhance`` sums onto its per-camera baseline."""
        ...


class ClassicalToneEstimator:
    """Deterministic tone deltas from classical CV — no model, fully offline."""

    def estimate(self, image: bytes) -> ToneParams:
        gray = decode_gray(image) / 255.0
        color = decode_color(image)
        return ToneParams(
            exposure_ev=clamp(self._exposure_ev(gray), EXPOSURE_EV_BOUND),
            temp_shift_k=clamp(self._temp_shift_k(color), TEMP_SHIFT_K_BOUND),
            shadows=clamp(self._shadows(gray), SHADOWS_BOUND),
            highlights=clamp(self._highlights(gray), HIGHLIGHTS_BOUND),
        )

    @staticmethod
    def _exposure_ev(gray: np.ndarray) -> float:
        """EV to pull the mean luminance toward the target — ``log2`` of the ratio,
        exactly ``compute_camera_baseline``'s brightness math."""
        mean = float(np.mean(gray))
        if mean <= 0.0:
            return EXPOSURE_EV_BOUND  # a black frame: brighten as hard as allowed
        return float(np.log2(_TARGET_LUMA / mean))

    @staticmethod
    def _temp_shift_k(color: np.ndarray) -> float:
        """Gray-world white balance: a blue cast (mean blue above mean red) warms
        the frame (positive Kelvin), a red cast cools it."""
        mean_b, _, mean_r = (float(np.mean(color[:, :, ch])) for ch in range(3))
        average = float(np.mean(color))
        if average <= 0.0:
            return 0.0
        return ((mean_b - mean_r) / average) * _TEMP_CAST_GAIN

    @staticmethod
    def _shadows(gray: np.ndarray) -> float:
        """Lift shadows in proportion to how much of the frame is crushed to black."""
        return float(np.mean(gray < _BLACK_CLIP)) * _SHADOW_GAIN

    @staticmethod
    def _highlights(gray: np.ndarray) -> float:
        """Recover (negative) highlights in proportion to how much is blown to white."""
        return -float(np.mean(gray > _WHITE_CLIP)) * _HIGHLIGHT_GAIN


__all__ = ["ClassicalToneEstimator", "ToneEstimator"]
