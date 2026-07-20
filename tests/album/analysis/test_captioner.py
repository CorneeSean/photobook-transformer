"""Tests for the captioner seam (step 29): the offline
:class:`~album.analysis.captioner.TemplateCaptioner` default, the opt-in
:class:`~album.analysis.captioner.ClaudeCaptioner`, the registry that selects between
them per run, and the A/B comparison the two enable.

Because tone is always classical (deterministic), the only axis that varies between
captioners is the caption itself — so an A/B test fixes the image + context and
asserts the two strategies produce distinct, each-valid captions. ``ClaudeCaptioner``
is driven by PydanticAI ``TestModel``/``FunctionModel`` (working-agreement.md §2.6),
so the online side runs in-suite with no API key.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from album.analysis.captioner import (
    Captioner,
    ClaudeCaptioner,
    TemplateCaptioner,
    build_captioner,
    captioner_names,
)
from album.analysis.context import PhotoContext
from tests.doubles import (
    caption_test_model,
    capturing_caption_media_types,
    capturing_caption_model,
)

# Minimal valid magic-byte prefixes — enough for the sniffer, which reads only the header.
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

_IMAGE = b"fake-thumbnail-bytes"
_WHEN = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def _full_context() -> PhotoContext:
    return PhotoContext(
        segment_label="Harbour walk",
        segment_place="Lisbon",
        person_names=["Sam", "Alex"],
        photo_place="Lisbon, Portugal",
        capture_time=_WHEN,
    )


class TestTemplateCaptioner:
    """Offline who/where/when composition — pure, deterministic, no model."""

    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            (["Sam"], "Sam"),
            (["Sam", "Alex"], "Sam and Alex"),
            (["Sam", "Alex", "Mia"], "Sam, Alex and Mia"),
        ],
        ids=["one", "two", "three"],
    )
    def test_name_grammar(self, names: list[str], expected: str) -> None:
        caption = TemplateCaptioner().caption(_IMAGE, PhotoContext(person_names=names))
        assert caption.startswith(expected)

    def test_full_who_where_when(self) -> None:
        caption = TemplateCaptioner().caption(_IMAGE, _full_context())
        assert caption == "Sam and Alex — Harbour walk, Lisbon, Portugal, May 2026"

    def test_location_precedence_photo_over_segment(self) -> None:
        # The photo's own geocoded place is more specific than the segment's place.
        ctx = PhotoContext(segment_place="Lisbon", photo_place="Lisbon, Portugal")
        assert "Lisbon, Portugal" in TemplateCaptioner().caption(_IMAGE, ctx)

    def test_duplicate_components_deduped(self) -> None:
        # segment_label == location must not read as "Lisbon, Lisbon".
        ctx = PhotoContext(segment_label="Lisbon", photo_place="Lisbon")
        assert TemplateCaptioner().caption(_IMAGE, ctx).count("Lisbon") == 1

    def test_when_only(self) -> None:
        assert TemplateCaptioner().caption(_IMAGE, PhotoContext(capture_time=_WHEN)) == "May 2026"

    def test_empty_context_falls_back_nonempty(self) -> None:
        caption = TemplateCaptioner().caption(_IMAGE, PhotoContext())
        assert caption.strip()
        assert "context" not in caption.lower()  # never apologises for missing grounding

    @pytest.mark.parametrize(
        "ctx",
        [
            PhotoContext(
                segment_label="Sunset sail",
                photo_place="Lisbon, Portugal",
                person_names=["Sam", "Alex"],
                capture_time=_WHEN,
            ),
            PhotoContext(
                segment_label="Ceremony",
                photo_place="Napa, California",
                person_names=["The Smiths"],
                capture_time=_WHEN,
            ),
        ],
        ids=["honeymoon", "wedding"],
    )
    def test_genericity_across_album_shapes(self, ctx: PhotoContext) -> None:
        # The same composer reads well for both album shapes (AGENTS.md rule 1).
        caption = TemplateCaptioner().caption(_IMAGE, ctx)
        assert caption.strip() and "—" in caption


class TestClaudeCaptioner:
    """Opt-in online captioner, driven by model doubles (no API key)."""

    def test_returns_caption(self) -> None:
        captioner = ClaudeCaptioner(model=caption_test_model("A quiet harbour at dusk."))
        assert captioner.caption(_IMAGE, PhotoContext()) == "A quiet harbour at dusk."

    def test_grounding_lines_reach_the_prompt(self) -> None:
        model, prompts = capturing_caption_model()
        ClaudeCaptioner(model=model).caption(_IMAGE, _full_context())
        joined = "\n".join(prompts)
        assert "Sam" in joined and "Lisbon, Portugal" in joined and "May 2026" in joined

    def test_from_settings_builds_without_a_key(self) -> None:
        # Building the opt-in captioner must not require ANTHROPIC_API_KEY — only a
        # real caption() call would (same posture as agents/_model.default_model).
        assert isinstance(ClaudeCaptioner.from_settings(), ClaudeCaptioner)

    @pytest.mark.parametrize(
        ("image", "expected_type"),
        [
            (_JPEG_BYTES, "image/jpeg"),
            (_WEBP_BYTES, "image/webp"),
            (_PNG_BYTES, "image/png"),
        ],
        ids=["jpeg", "webp", "png"],
    )
    def test_media_type_matches_actual_bytes(self, image: bytes, expected_type: str) -> None:
        # Immich thumbnails come back JPEG *or* WebP; declaring a wrong media_type makes
        # the Anthropic API reject the request (image/jpeg vs actual image/webp), so the
        # type must be sniffed from the bytes, not hardcoded.
        model, media_types = capturing_caption_media_types()
        ClaudeCaptioner(model=model).caption(image, PhotoContext())
        assert media_types == [expected_type]

    def test_blank_model_output_raises(self) -> None:
        # The Captioner contract is a non-empty caption (the retired AnalysisResult
        # enforced it via Field(min_length=1)); a whitespace-only model response is a
        # per-photo failure the stage isolates, not a silent empty caption.
        with pytest.raises(ValueError, match="empty caption"):
            ClaudeCaptioner(model=caption_test_model("   ")).caption(_IMAGE, PhotoContext())


class TestCaptionerRegistry:
    """Per-run selection: name -> Captioner, the A/B enabler."""

    @pytest.mark.parametrize(
        ("name", "cls"),
        [("template", TemplateCaptioner), ("claude", ClaudeCaptioner)],
        ids=["template", "claude"],
    )
    def test_build(self, name: str, cls: type[Captioner]) -> None:
        assert isinstance(build_captioner(name), cls)

    def test_names_lists_both(self) -> None:
        assert captioner_names() == ["claude", "template"]

    def test_unknown_name_raises_clearly(self) -> None:
        with pytest.raises(ValueError, match="unknown captioner"):
            build_captioner("gpt")


class TestCaptionerConformance:
    """Both concretes satisfy the Captioner contract (the A/B axis endpoints)."""

    @pytest.mark.parametrize(
        "captioner",
        [TemplateCaptioner(), ClaudeCaptioner(model=caption_test_model("A candid moment."))],
        ids=["template", "claude"],
    )
    def test_returns_nonempty_caption(self, captioner: Captioner) -> None:
        assert captioner.caption(_IMAGE, _full_context()).strip()


class TestCaptionerABComparison:
    """The isolated A/B: same photo + context, two strategies, distinct valid output."""

    def test_strategies_diverge_on_same_input(self) -> None:
        context = _full_context()
        template = TemplateCaptioner().caption(_IMAGE, context)
        claude = ClaudeCaptioner(model=caption_test_model("A candid moment by the water.")).caption(
            _IMAGE,
            context,
        )
        assert template and claude and template != claude  # swapping strategy changes output
        assert "Lisbon" in template  # the offline side is metadata-grounded
