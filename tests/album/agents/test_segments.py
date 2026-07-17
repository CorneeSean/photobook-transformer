"""Tests for the segments-extraction agent (``agents/segments.py``), via
PydanticAI's ``TestModel``/``FunctionModel`` — no real Claude calls (build step
28 / Feature B of single-command-ingestion.md).

Test-first: the output validates through the reused ``SegmentYAML`` shape; the
grounding (capture range + timezone + document text) reaches the prompt; an empty
document yields an empty segment list; and the PDF readers extract text (and, for
a scanned/image-only PDF, page images) — with a real PDF built at test time via
``weasyprint`` (already a dependency), read back through ``pypdf``. Wiring in real
Claude and spot-checking accuracy is deferred (no ``ANTHROPIC_API_KEY`` here; see
progress.md, same pattern as ``analysis.py``).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from io import BytesIO

import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from album.agents.segments import (
    ExtractedSegments,
    SegmentExtractionError,
    extract_segments,
    extract_segments_from_pdf,
    read_pdf_images,
    read_pdf_text,
)

_CANNED_JSON = (
    '{"title": "Adriatic Days", "segments": ['
    '{"label": "Lisbon", "place": "Lisbon", '
    '"start_at": "2026-05-01T09:00:00+00:00", "end_at": "2026-05-01T18:00:00+00:00"}]}'
)


def _text_pdf(body: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=f"<html><body><p>{body}</p></body></html>").write_pdf() or b""


def _image_pdf() -> bytes:
    from PIL import Image
    from weasyprint import HTML

    buffer = BytesIO()
    Image.new("RGB", (32, 32), (200, 30, 30)).save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    return HTML(string=f'<html><body><img src="{data_uri}"></body></html>').write_pdf() or b""


def _blank_pdf() -> bytes:
    from weasyprint import HTML

    return HTML(string="<html><body></body></html>").write_pdf() or b""


class TestExtractSegments:
    """`extract_segments`: the pure agent call over prepared content."""

    def test_test_model_produces_valid_result(self) -> None:
        result = extract_segments("Day 1 — Lisbon.\nDay 2 — Perast.", model=TestModel())

        assert isinstance(result, ExtractedSegments)
        assert isinstance(result.segments, list)

    def test_grounding_and_document_reach_the_prompt(self) -> None:
        prompts: list[str] = []

        def handler(
            messages: list[ModelMessage],
            info: AgentInfo,
        ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
            for message in messages:
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        prompts.append(part.content)
                    elif isinstance(part, UserPromptPart):
                        prompts.extend(item for item in part.content if isinstance(item, str))
            return ModelResponse(parts=[TextPart(content=_CANNED_JSON)])

        capture_range = (
            datetime(2026, 5, 1, 8, tzinfo=UTC),
            datetime(2026, 5, 3, 20, tzinfo=UTC),
        )
        result = extract_segments(
            "Day 1 — Lisbon. Day 2 — Perast.",
            capture_range=capture_range,
            timezone="Europe/Podgorica",
            model=FunctionModel(handler),
        )

        joined = "\n".join(prompts)
        assert "2026-05-01T08:00:00+00:00" in joined  # capture-range anchor
        assert "Europe/Podgorica" in joined  # timezone anchor
        assert "Lisbon" in joined  # the document text itself
        # The canned response parsed into the reused SegmentYAML shape.
        assert result.title == "Adriatic Days"
        assert result.segments[0].label == "Lisbon"

    def test_empty_document_yields_empty_segments(self) -> None:
        def handler(
            messages: list[ModelMessage],
            info: AgentInfo,
        ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
            return ModelResponse(parts=[TextPart(content='{"title": null, "segments": []}')])

        result = extract_segments("(no schedule here)", model=FunctionModel(handler))

        assert result.title is None
        assert result.segments == []

    def test_image_content_is_passed_through(self) -> None:
        # A scanned document arrives as page images, not text — assert they reach
        # the model as BinaryContent parts alongside the grounding text.
        parts_seen: list[object] = []

        def handler(
            messages: list[ModelMessage],
            info: AgentInfo,
        ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
            for message in messages:
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                        parts_seen.extend(part.content)
            return ModelResponse(parts=[TextPart(content='{"segments": []}')])

        image = BinaryContent(data=b"\x89PNG-bytes", media_type="image/png")
        extract_segments([image], model=FunctionModel(handler))

        assert any(isinstance(part, BinaryContent) for part in parts_seen)


class TestReadPdf:
    """`read_pdf_text`/`read_pdf_images`: pypdf over real weasyprint-built PDFs."""

    def test_reads_text_from_a_text_pdf(self) -> None:
        text = read_pdf_text(_text_pdf("Day 1 Lisbon, Portugal. Day 2 Perast."))

        assert "Lisbon" in text
        assert "Perast" in text

    def test_scanned_pdf_has_no_text_but_yields_page_images(self) -> None:
        pdf = _image_pdf()

        assert read_pdf_text(pdf) == ""  # image-only → no extractable text
        images = read_pdf_images(pdf)
        assert len(images) >= 1
        assert images[0].media_type == "image/png"


class TestExtractSegmentsFromPdf:
    """`extract_segments_from_pdf`: text path, scanned fallback, and empty doc."""

    def test_uses_text_when_present(self) -> None:
        result = extract_segments_from_pdf(_text_pdf("Day 1 — Lisbon."), model=TestModel())

        assert isinstance(result, ExtractedSegments)

    def test_falls_back_to_page_images_for_a_scanned_pdf(self) -> None:
        # No text, but an embedded image → the image path runs (TestModel accepts
        # the image content and still returns a valid result).
        result = extract_segments_from_pdf(_image_pdf(), model=TestModel())

        assert isinstance(result, ExtractedSegments)

    def test_raises_when_document_has_neither_text_nor_images(self) -> None:
        with pytest.raises(SegmentExtractionError, match="no extractable"):
            extract_segments_from_pdf(_blank_pdf(), model=TestModel())
