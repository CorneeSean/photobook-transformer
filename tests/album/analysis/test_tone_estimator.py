"""Tests for :class:`~album.analysis.tone_estimator.ClassicalToneEstimator` — the
offline CV replacement for the LLM's tone deltas (step 29).

Deterministic, no model: synthetic images with a known defect assert the *sign* of
the corresponding correction, a neutral image asserts no spurious correction, and a
real photo fixture asserts the estimate stays within the tone bounds
``enhance``/``ToneParams`` enforce. Image ops are tested against committed/synthetic
fixture images (working-agreement.md §2.6 test-strategy table).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from skimage import data as skimage_data

from album.analysis.tone import (
    EXPOSURE_EV_BOUND,
    HIGHLIGHTS_BOUND,
    SHADOWS_BOUND,
    TEMP_SHIFT_K_BOUND,
    ToneParams,
)
from album.analysis.tone_estimator import ClassicalToneEstimator


def _png(array: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", array)  # lossless — no JPEG artefacts to flake on
    assert ok
    return buffer.tobytes()


class TestClassicalToneEstimator:
    """The four estimated knobs move in the right direction for a known defect."""

    _SIZE = 32

    def _solid_gray(self, value: int) -> bytes:
        return _png(np.full((self._SIZE, self._SIZE), value, dtype=np.uint8))

    def _blue_cast(self) -> bytes:
        arr = np.zeros((self._SIZE, self._SIZE, 3), dtype=np.uint8)
        arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = 200, 128, 80  # BGR: heavy blue, weak red
        return _png(arr)

    def _crushed_shadows(self) -> bytes:
        arr = np.full((self._SIZE, self._SIZE), 230, dtype=np.uint8)
        arr[: self._SIZE // 2, :] = 0  # half the frame clipped to black
        return _png(arr)

    def _blown_highlights(self) -> bytes:
        arr = np.full((self._SIZE, self._SIZE), 28, dtype=np.uint8)
        arr[: self._SIZE // 2, :] = 255  # half the frame clipped to white
        return _png(arr)

    def test_dark_image_brightens(self) -> None:
        tone = ClassicalToneEstimator().estimate(self._solid_gray(30))
        assert tone.exposure_ev > 0.0

    def test_blue_cast_warms(self) -> None:
        tone = ClassicalToneEstimator().estimate(self._blue_cast())
        assert tone.temp_shift_k > 0.0  # positive = warmer, counters the blue

    def test_crushed_shadows_lift(self) -> None:
        tone = ClassicalToneEstimator().estimate(self._crushed_shadows())
        assert tone.shadows > 0.0

    def test_blown_highlights_recover(self) -> None:
        tone = ClassicalToneEstimator().estimate(self._blown_highlights())
        assert tone.highlights < 0.0  # negative = recovery

    def test_neutral_midgray_is_near_zero(self) -> None:
        # A flat mid-grey frame has no defect — every knob should stay ~0 so the
        # estimator never invents a correction (AGENTS.md rule 5).
        tone = ClassicalToneEstimator().estimate(self._solid_gray(115))
        assert tone.exposure_ev == pytest.approx(0.0, abs=0.1)
        assert tone.temp_shift_k == pytest.approx(0.0, abs=1.0)
        assert tone.shadows == pytest.approx(0.0, abs=1e-9)
        assert tone.highlights == pytest.approx(0.0, abs=1e-9)

    def test_within_tone_bounds(self) -> None:
        # Even a pathological all-black frame stays inside the bounds reconcile_tone
        # relies on (so baseline + delta math never overflows them).
        tone = ClassicalToneEstimator().estimate(self._solid_gray(0))
        assert isinstance(tone, ToneParams)
        assert -EXPOSURE_EV_BOUND <= tone.exposure_ev <= EXPOSURE_EV_BOUND
        assert -TEMP_SHIFT_K_BOUND <= tone.temp_shift_k <= TEMP_SHIFT_K_BOUND
        assert -SHADOWS_BOUND <= tone.shadows <= SHADOWS_BOUND
        assert -HIGHLIGHTS_BOUND <= tone.highlights <= HIGHLIGHTS_BOUND

    def test_real_photo_fixture_sanity(self) -> None:
        # A real photograph: a modest, finite, in-bounds estimate (not a wild swing).
        rgb = skimage_data.astronaut()
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        tone = ClassicalToneEstimator().estimate(_png(bgr))
        assert -EXPOSURE_EV_BOUND <= tone.exposure_ev <= EXPOSURE_EV_BOUND
        assert -TEMP_SHIFT_K_BOUND <= tone.temp_shift_k <= TEMP_SHIFT_K_BOUND
