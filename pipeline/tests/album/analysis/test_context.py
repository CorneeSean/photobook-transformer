"""Tests for :mod:`album.analysis.context` — ``PhotoContext`` and the pure prompt
builder ``format_context`` (the text half of ``ClaudeCaptioner``'s prompt).

Moved from the retired ``agents/analysis.py`` when step 29 split the fused vision
call; the ``AnalysisResult``/``analyze_photo`` cases went with it (tone is now
classical, caption is a ``Captioner``). Grounding lines are emitted only when the
album has that grounding — the genericity guard.
"""

from __future__ import annotations

from datetime import UTC, datetime

from album.analysis.context import PhotoContext, format_context, format_when

_WHEN = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


class TestFormatContext:
    """`format_context`: the pure prompt builder over optional grounding."""

    def test_segment_label_and_place(self) -> None:
        prompt = format_context(PhotoContext(segment_label="Day 4", segment_place="Lisbon"))
        assert "Context: Day 4 in Lisbon." in prompt

    def test_segment_label_without_place(self) -> None:
        prompt = format_context(PhotoContext(segment_label="Day 4"))
        assert "Context: Day 4." in prompt

    def test_location_line(self) -> None:
        prompt = format_context(PhotoContext(photo_place="Lisbon, Portugal"))
        assert "Location: Lisbon, Portugal." in prompt

    def test_people_line(self) -> None:
        prompt = format_context(PhotoContext(person_names=["Alex", "Sam"]))
        assert "People in this photo: Alex, Sam." in prompt

    def test_when_line(self) -> None:
        prompt = format_context(PhotoContext(capture_time=_WHEN))
        assert "Taken: May 2026." in prompt

    def test_meta_line(self) -> None:
        prompt = format_context(PhotoContext(album_meta={"occasion": "honeymoon"}))
        assert "Album context:" in prompt

    def test_no_context_yields_only_the_base_line(self) -> None:
        # Genericity guard: no grounding ⇒ just the instruction, no dangling lines.
        assert format_context(PhotoContext()) == "Analyze this photo."


class TestFormatWhen:
    def test_month_year(self) -> None:
        assert format_when(_WHEN) == "May 2026"
