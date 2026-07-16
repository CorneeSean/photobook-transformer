"""Test doubles shared across more than one test file.

Kept here rather than duplicated per file once a second file needs the same
double (quality-practices.md §2.4). The vision-call stages (photo_analyzer,
enhance, caption) share the same :class:`ThumbnailSource` fake and PydanticAI
stand-in models; ``quality``'s :class:`AestheticScorer`/``Embedder`` doubles
are shared between ``test_quality.py`` and the flow concurrency test.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel


class FakeThumbnails:
    """Serves canned thumbnail bytes keyed by asset id, recording every fetch."""

    def __init__(
        self, by_asset: dict[str, bytes] | None = None, *, default: bytes = b"img"
    ) -> None:
        self._by_asset = by_asset or {}
        self._default = default
        self.calls: list[str] = []

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        self.calls.append(asset_id)
        return self._by_asset.get(asset_id, self._default)


class ConstantAesthetic:
    """A fixed-score :class:`~album.stages.quality.AestheticScorer` double,
    counting calls so tests can assert idempotency (not re-scored on a rerun)."""

    def __init__(self, value: float = 0.5) -> None:
        self.value = value
        self.calls = 0

    def score(self, image: bytes) -> float:
        self.calls += 1
        return self.value


class ConstantEmbedder:
    """A fixed-embedding :class:`~album.stages.quality.Embedder` double (the
    768-dim width matches data-model.md's ``Vector(768)``), counting calls."""

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, image: bytes) -> list[float]:
        self.calls += 1
        return [0.1] * 768


_CANNED_ANALYSIS_JSON = (
    '{"exposure_ev": 0.0, "temp_shift_k": 0.0, "shadows": 0.0, '
    '"highlights": 0.0, "caption": "A generic caption."}'
)


def canned_analysis_response() -> ModelResponse:
    """The canonical valid :class:`AnalysisResult` as a raw ``ModelResponse`` —
    the single source of the canned JSON shared by every ``FunctionModel``
    double (the call-counter here, the prompt-capturer in
    ``test_photo_analyzer.py``), so the literal lives in exactly one place."""
    return ModelResponse(parts=[TextPart(content=_CANNED_ANALYSIS_JSON)])


def analysis_test_model(**overrides: object) -> TestModel:
    """A :class:`TestModel` returning a valid :class:`AnalysisResult`, tone deltas
    zeroed by default; pass overrides (e.g. ``exposure_ev=0.2``) for tests that
    assert on specific tone math."""
    args: dict[str, object] = {
        "exposure_ev": 0.0,
        "temp_shift_k": 0.0,
        "shadows": 0.0,
        "highlights": 0.0,
        "caption": "A generic caption.",
    }
    args.update(overrides)
    return TestModel(custom_output_args=args)


def counting_analysis_model() -> tuple[FunctionModel, list[int]]:
    """A :class:`FunctionModel` plus a list that records one entry per call —
    used to prove the shared vision call happens at most once per photo."""
    calls: list[int] = []

    def handler(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
        calls.append(1)
        return canned_analysis_response()

    return FunctionModel(handler), calls
