"""Test doubles shared across more than one test file.

Kept here rather than duplicated per file once a second file needs the same
double (quality-practices.md §2.4). The analysis stages (photo_analyzer, enhance,
caption) share the :class:`ThumbnailSource` fake plus the tone/caption collaborator
doubles below; ``quality``'s :class:`AestheticScorer`/``Embedder`` doubles are shared
between ``test_quality.py`` and the flow concurrency test.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic_ai import BinaryContent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from album.analysis.context import PhotoContext
from album.analysis.tone import ToneParams
from album.immich.client import ImmichAsset, ImmichJobCounts


class FakeThumbnails:
    """Serves canned thumbnail bytes keyed by asset id, recording every fetch."""

    def __init__(
        self,
        by_asset: dict[str, bytes] | None = None,
        *,
        default: bytes = b"img",
    ) -> None:
        self._by_asset = by_asset or {}
        self._default = default
        self.calls: list[str] = []

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        self.calls.append(asset_id)
        return self._by_asset.get(asset_id, self._default)


class ConstantAesthetic:
    """A fixed-score :class:`~album.flow.stages.quality.AestheticScorer` double,
    counting calls so tests can assert idempotency (not re-scored on a rerun)."""

    def __init__(self, value: float = 0.5) -> None:
        self.value = value
        self.calls = 0

    def score(self, image: bytes) -> float:
        self.calls += 1
        return self.value


class ConstantEmbedder:
    """A fixed-embedding :class:`~album.flow.stages.quality.Embedder` double (the
    768-dim width matches data-model.md's ``Vector(768)``), counting calls."""

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, image: bytes) -> list[float]:
        self.calls += 1
        return [0.1] * 768


class FixedTone:
    """A :class:`~album.analysis.tone_estimator.ToneEstimator` double returning a
    fixed delta, so persistence / reconciliation math is observable independent of
    any real image."""

    def __init__(self, tone: ToneParams) -> None:
        self._tone = tone

    def estimate(self, image: bytes) -> ToneParams:
        return self._tone


class ZeroToneEstimator(FixedTone):
    """:class:`FixedTone` with an all-zero delta — the common "don't drag real CV
    into this test" case (isolates caption/analyzer behaviour)."""

    def __init__(self) -> None:
        super().__init__(ToneParams())


class RecordingCaptioner:
    """A :class:`~album.analysis.captioner.Captioner` double: returns a canned
    caption and records each ``(image, context)`` call — a deterministic fake, not a
    mock, so tests can assert it ran the right number of times (idempotency, A/B)."""

    def __init__(self, caption: str = "A generic caption.") -> None:
        self._caption = caption
        self.calls: list[tuple[bytes, PhotoContext]] = []

    def caption(self, image: bytes, context: PhotoContext) -> str:
        self.calls.append((image, context))
        return self._caption


def caption_test_model(text: str = "A generic caption.") -> TestModel:
    """A :class:`TestModel` for ``ClaudeCaptioner`` (plain-text ``output_type=str``),
    returning ``text`` without touching the Claude API."""
    return TestModel(custom_output_text=text)


def capturing_caption_model(
    caption: str = "A candid moment.",
) -> tuple[FunctionModel, list[str]]:
    """A :class:`FunctionModel` plus a list capturing the text of each user prompt it
    receives, so a test can assert what grounding ``ClaudeCaptioner`` put in the
    prompt. ``ClaudeCaptioner`` always sends ``[format_context(...), BinaryContent]``,
    so the user part's content is a sequence — pull out its text element(s)."""
    prompts: list[str] = []

    def handler(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
        for message in messages:
            for part in message.parts:
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                    prompts.extend(item for item in part.content if isinstance(item, str))
        return ModelResponse(parts=[TextPart(content=caption)])

    return FunctionModel(handler), prompts


def capturing_caption_system_prompt(
    caption: str = "A candid moment.",
) -> tuple[FunctionModel, list[str]]:
    """A :class:`FunctionModel` plus a list capturing the text of each system prompt it
    receives, so a test can assert the ``ClaudeCaptioner`` system prompt is specialised
    to the requested output language (and enforces one short sentence)."""
    system_prompts: list[str] = []

    def handler(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
        for message in messages:
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    system_prompts.append(part.content)
        return ModelResponse(parts=[TextPart(content=caption)])

    return FunctionModel(handler), system_prompts


def capturing_caption_media_types(
    caption: str = "A candid moment.",
) -> tuple[FunctionModel, list[str]]:
    """A :class:`FunctionModel` plus a list capturing the ``media_type`` of every
    :class:`BinaryContent` image ``ClaudeCaptioner`` sends, so a test can assert the
    declared type matches the actual bytes (Immich thumbnails may be JPEG or WebP)."""
    media_types: list[str] = []

    def handler(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
        for message in messages:
            for part in message.parts:
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                    media_types.extend(
                        item.media_type for item in part.content if isinstance(item, BinaryContent)
                    )
        return ModelResponse(parts=[TextPart(content=caption)])

    return FunctionModel(handler), media_types


class DatingIngestClient:
    """A fake :class:`~album.ingest.IngestClient` that dates each uploaded photo by
    filename, so folder-derived segment windows are deterministic.
    ``get_album_assets`` echoes the uploaded photos with their local capture time
    (``None`` for a filename not in ``times`` → an undated photo). Shared by
    ``test_ingest.py`` (the folder-segment + fan-out tests) and ``test_genericity.py``
    (the end-to-end fan-out), so it lives here per quality-practices.md §2.4."""

    def __init__(self, times: dict[str, datetime]) -> None:
        self._times = times
        self.uploaded_ids: dict[Path, str] = {}

    def upload_asset(self, path: Path) -> str:
        asset_id = f"asset-{len(self.uploaded_ids) + 1}"
        self.uploaded_ids[path] = asset_id
        return asset_id

    def create_album(self, name: str, asset_ids: list[str]) -> str:
        return "immich-new-1"

    def get_jobs(self) -> dict[str, ImmichJobCounts]:
        return {}

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]:
        return [
            ImmichAsset(
                id=asset_id,
                originalFileName=path.name,
                localDateTime=self._times.get(path.name),
            )
            for path, asset_id in self.uploaded_ids.items()
        ]
