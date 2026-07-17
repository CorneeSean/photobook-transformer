"""Per-photo analysis collaborators: the classical tone estimator and the
captioner seam (step 29). See [local-analysis.md](/build/local-analysis.md)."""

from __future__ import annotations

from album.analysis.captioner import (
    Captioner,
    ClaudeCaptioner,
    TemplateCaptioner,
    build_captioner,
    captioner_names,
)
from album.analysis.context import PhotoContext, format_context
from album.analysis.tone import ToneParams
from album.analysis.tone_estimator import ClassicalToneEstimator, ToneEstimator

__all__ = [
    "Captioner",
    "ClassicalToneEstimator",
    "ClaudeCaptioner",
    "PhotoContext",
    "TemplateCaptioner",
    "ToneEstimator",
    "ToneParams",
    "build_captioner",
    "captioner_names",
    "format_context",
]
