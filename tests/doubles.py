"""Test doubles shared across the vision-call stages (photo_analyzer, enhance,
caption).

Kept here rather than duplicated per file because all three exercise the same
:class:`~album.photo_analyzer.PhotoAnalyzer` path and so need the same
:class:`ThumbnailSource` fake and the same PydanticAI stand-in models
(quality-practices.md §2.4 — a double shared across files lives in one place).
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
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        '{"exposure_ev": 0.0, "temp_shift_k": 0.0, "shadows": 0.0, '
                        '"highlights": 0.0, "caption": "A generic caption."}'
                    )
                )
            ]
        )

    return FunctionModel(handler), calls
