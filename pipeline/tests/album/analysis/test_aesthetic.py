"""Tests for :mod:`album.analysis.aesthetic` — the real LAION/open_clip scorer.

Two tiers:

- **Pure, torch-free** (runs in the default ``poe check``): the ``_check_dim`` shape
  guard and the capability/fail-fast checks, which never import torch. Importing the
  module is deliberately torch-free (all heavy imports are lazy, inside methods).
- **The real model** (``TestClipAestheticModel``): opt-in and heavy — it loads
  ViT-L/14. Skipped unless the ``scoring`` extra is installed **and** the weights are
  fetched **and** ``SCORING_INTEGRATION=1`` (env-gated exactly like step 24's Immich
  ML test), so the default gate neither imports torch nor downloads weights. Run via
  ``uv run poe test-scoring`` after ``uv sync --extra scoring`` +
  ``uv run poe fetch-aesthetic-weights``.
"""

from __future__ import annotations

import math
import os
from importlib.util import find_spec
from pathlib import Path

import pytest

from album.analysis.aesthetic import (
    ClipAestheticModel,
    _check_dim,
    _require_capability,
    _require_supported_device,
    _require_usable_precision,
)
from album.config import QualitySettings

_IMAGES = Path(__file__).parent.parent.parent / "fixtures" / "images"
_SHARP = (_IMAGES / "sharp.jpg").read_bytes()
_BLURRY = (_IMAGES / "blurry.jpg").read_bytes()

_SETTINGS = QualitySettings()


def _weights_present() -> bool:
    return (Path(_SETTINGS.weights_dir) / _SETTINGS.aesthetic_weights).exists()


_real_model = pytest.mark.skipif(
    os.environ.get("SCORING_INTEGRATION") != "1"
    or find_spec("torch") is None
    or find_spec("open_clip") is None
    or not _weights_present(),
    reason="needs the 'scoring' extra + fetched weights + SCORING_INTEGRATION=1 "
    "(uv sync --extra scoring; uv run poe fetch-aesthetic-weights)",
)


class TestCheckDim:
    """The fail-loud shape guard — torch-free, so it runs in the default gate."""

    def test_passes_a_768_vector_through_unchanged(self) -> None:
        values = [0.1] * 768
        assert _check_dim(values) is values

    def test_rejects_a_wrong_length_embedding(self) -> None:
        with pytest.raises(ValueError, match="768"):
            _check_dim([0.1] * 512)


class TestRequireCapability:
    """Construction-time fail-fast — all cheap, none import torch."""

    def test_rejects_fp16_on_cpu(self) -> None:
        # Call the sub-check directly (like the device test below), so it's isolated
        # from `_require_capability`'s torch/extra check — the precision rule holds
        # whether or not the `scoring` extra is installed.
        with pytest.raises(ValueError, match="fp16"):
            _require_usable_precision(QualitySettings(precision="fp16", device="cpu"))

    def test_rejects_an_unsupported_device(self) -> None:
        # A bogus QUALITY__DEVICE fails at construction (one clean fallback) rather than
        # blowing up torch.device() on every photo.
        with pytest.raises(ValueError, match="device"):
            _require_supported_device(QualitySettings(device="gpu"))

    def test_missing_weights_raise_a_clear_error(self, tmp_path: Path) -> None:
        settings = QualitySettings(weights_dir=str(tmp_path))
        # torch/open_clip may or may not be installed here; if they are, the next
        # failing check is the (absent) weights file — which is what we assert.
        if find_spec("torch") is None or find_spec("open_clip") is None:
            with pytest.raises(ImportError, match="scoring"):
                _require_capability(settings)
        else:
            with pytest.raises(FileNotFoundError, match="weights"):
                _require_capability(settings)


@_real_model
class TestClipAestheticModel:
    """The real ViT-L/14 model — opt-in, heavy, skipped in the default gate."""

    def test_embed_returns_768_finite_floats(self) -> None:
        embedding = ClipAestheticModel(_SETTINGS).embed(_SHARP)
        assert len(embedding) == 768
        assert all(math.isfinite(x) for x in embedding)

    def test_score_is_finite_and_ranks_sharp_above_blurry(self) -> None:
        model = ClipAestheticModel(_SETTINGS)
        sharp = model.score(_SHARP)
        blurry = model.score(_BLURRY)
        assert math.isfinite(sharp) and math.isfinite(blurry)
        # Monotonicity sanity (not a golden value): a blurred copy of the same frame
        # is aesthetically worse, so it must score strictly lower.
        assert sharp > blurry

    def test_score_then_embed_on_the_same_bytes_forwards_once(self) -> None:
        # The one-entry feature memo means score()+embed() on the same thumbnail runs
        # the CLIP forward a single time (the whole point of caching the features).
        model = ClipAestheticModel(_SETTINGS)
        model.score(_SHARP)
        model.embed(_SHARP)
        assert model._forward_count == 1  # pyright: ignore[reportPrivateUsage]
        # A different image evicts the one-entry memo → a second forward.
        model.score(_BLURRY)
        assert model._forward_count == 2  # pyright: ignore[reportPrivateUsage]
