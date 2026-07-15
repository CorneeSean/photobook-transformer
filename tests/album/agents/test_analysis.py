"""Tests for the shared vision agent (``agents/analysis.py``), via PydanticAI's
``TestModel`` — no real Claude calls (build step 9).

Test-first: the output model validates and rejects out-of-bounds delta values,
and the agent handles a photo with **no segment context** without erroring,
still producing a caption. Wiring in real Claude and sanity-checking ~20
photos is deferred — no ``ANTHROPIC_API_KEY`` in this environment; see
progress.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from album.agents.analysis import AnalysisResult, PhotoContext, analyze_photo, format_context

_IMAGE = b"fake-thumbnail-bytes"

_VALID_KWARGS: dict[str, object] = {
    "exposure_ev": 0.0,
    "temp_shift_k": 0.0,
    "shadows": 0.0,
    "highlights": 0.0,
    "caption": "x",
}


class TestAnalysisResultBounds:
    """The output model rejects out-of-bounds tone deltas (AGENTS.md rule 5)."""

    @pytest.mark.parametrize(
        ("field_name", "bad_value"),
        [
            ("exposure_ev", 5.0),  # > le=2.0
            ("temp_shift_k", 5000.0),  # > le=2000.0
            ("shadows", 2.0),  # > le=1.0
            ("highlights", -2.0),  # < ge=-1.0
            ("caption", ""),  # < min_length=1
        ],
    )
    def test_rejects_out_of_bounds_values(self, field_name: str, bad_value: object) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult(**{**_VALID_KWARGS, field_name: bad_value})  # pyright: ignore[reportArgumentType]


class TestAnalyzePhoto:
    """`analyze_photo`: produces a valid, bounded result via TestModel."""

    def test_with_test_model_produces_valid_result(self) -> None:
        result = analyze_photo(_IMAGE, PhotoContext(), model=TestModel())

        assert isinstance(result, AnalysisResult)
        assert -2.0 <= result.exposure_ev <= 2.0
        assert -2000.0 <= result.temp_shift_k <= 2000.0
        assert -1.0 <= result.shadows <= 1.0
        assert -1.0 <= result.highlights <= 1.0
        assert result.caption

    def test_without_segment_context_still_produces_caption(self) -> None:
        # No segment, no album meta — the genericity guard for this step: it must
        # not error just because the album has no structure to ground with.
        result = analyze_photo(_IMAGE, PhotoContext(), model=TestModel())

        assert isinstance(result, AnalysisResult)
        assert result.caption


class TestFormatContext:
    """`format_context`: the pure prompt builder over optional grounding."""

    def test_includes_segment_label_and_place(self) -> None:
        context = PhotoContext(segment_label="Day 4", segment_place="Lisbon")

        prompt = format_context(context)

        assert "Day 4" in prompt
        assert "Lisbon" in prompt

    def test_includes_album_meta(self) -> None:
        context = PhotoContext(album_meta={"occasion": "honeymoon", "names": ["Alex", "Sam"]})

        prompt = format_context(context)

        assert "honeymoon" in prompt

    def test_with_no_context_is_still_a_valid_prompt(self) -> None:
        prompt = format_context(PhotoContext())

        assert prompt  # non-empty; the agent still has something to work from
        assert "Context:" not in prompt  # no segment info leaked in when there is none
