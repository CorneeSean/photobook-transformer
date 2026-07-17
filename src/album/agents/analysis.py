"""The single combined vision agent: bounded tone deltas + a caption, one call.

Per [Architecture](/foundations/architecture.md)'s "one combined vision call" — `enhance`
and `caption` both need a downscaled thumbnail of the same selected photo, and
uploading it is the expensive part, so one agent produces both outputs from
one request.

**AI estimates parameters, never generates pixels** (AGENTS.md rule 5):
`AnalysisResult`'s tone deltas are bounded (`Field(ge=..., le=...)`), so a bad
model call can be slightly wrong but never drastic; classical code
(`enhance`/`render`) applies them.

This module is deliberately DB-free and side-effect-free — no ``Session``, no
``album_id``. Persisting the result and skipping an already-analyzed photo
(the "idempotent, once per photo" behavior architecture.md describes) is each
calling stage's job (`enhance` in step 10, `caption` in step 12), the same
split as ``immich/client.py`` (pure API wrapper) vs. ``stages/sync.py`` (owns
the DB side).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model

from album.agents._model import default_model

_SYSTEM_PROMPT = """\
You are a photo-book curation assistant. Given one photo, produce two things
in a single response:

1. Bounded tone-correction estimates: small numeric deltas a classical
   rendering pipeline will apply on top of the original. Estimate only —
   never describe or imply pixels you would invent; the deltas are the only
   output that reaches the image.
2. A short, natural caption. Ground it in whatever context you're given (a
   segment name/place, the names of people in the photo, album metadata) when
   present. When names are given, use them naturally; never invent a name, and
   never state that you don't know who someone is. If no context is given, still
   write an accurate, general caption from the image alone — never mention that
   context is missing.
"""


class AnalysisResult(BaseModel):
    """Bounded tone-correction deltas plus a caption, from one vision call.

    Field bounds mirror the ``Photo.enhance_*`` columns these are written to
    (data-model.md) — a malformed model response is rejected by Pydantic
    rather than silently clamped or trusted.
    """

    exposure_ev: float = Field(ge=-2.0, le=2.0, description="Exposure correction, in stops.")
    temp_shift_k: float = Field(
        ge=-2000.0, le=2000.0, description="White-balance shift, in Kelvin."
    )
    shadows: float = Field(
        ge=-1.0, le=1.0, description="Shadow lift (positive) or crush (negative)."
    )
    highlights: float = Field(
        ge=-1.0, le=1.0, description="Highlight recovery (negative) or blow-out (positive)."
    )
    caption: str = Field(min_length=1, description="A short, natural caption for the photo.")


@dataclass
class PhotoContext:
    """Whatever grounding the album has for one photo — all optional, since
    [`Segment`](/foundations/domain-model.md) itself is optional. An album with none of
    this still produces a caption, just a more generic one."""

    segment_label: str | None = None
    segment_place: str | None = None
    album_meta: dict[str, Any] = field(default_factory=dict)
    # Display names of the *named* people in this photo, resolved from
    # ``Photo.person_ids`` — see ``PhotoAnalyzer._resolve_person_names`` for how,
    # and for why an empty list leaves the prompt unchanged (genericity guard).
    person_names: list[str] = field(default_factory=list)
    # The photo's reverse-geocoded place (Immich ``city``/``state``/``country``,
    # joined by ``PhotoAnalyzer._format_place``) — the most specific location
    # grounding, below the segment. ``None`` when the photo has no geocoded place,
    # leaving the prompt unchanged (genericity guard). See enrichment.md step 20.
    photo_place: str | None = None


_agent = Agent(output_type=AnalysisResult, system_prompt=_SYSTEM_PROMPT)


def analyze_photo(
    image: bytes,
    context: PhotoContext,
    *,
    media_type: str = "image/jpeg",
    model: Model | None = None,
) -> AnalysisResult:
    """Run the shared vision call for one photo's thumbnail. Pure — no DB
    reads/writes, no idempotency check (callers own that). ``model`` defaults
    to real Claude (via ``config.py``'s ``anthropic_api_key``); tests inject
    PydanticAI's ``TestModel``/``FunctionModel`` instead."""
    prompt = [format_context(context), BinaryContent(data=image, media_type=media_type)]
    result = _agent.run_sync(prompt, model=model or default_model())
    return result.output


def format_context(context: PhotoContext) -> str:
    """Build the text half of the prompt from whatever grounding the album has.
    Standalone and pure so it's directly unit-testable, like ``quality.py``'s
    ``compute_blur_score``."""
    return "\n".join(["Analyze this photo.", *_grounding_lines(context)])


def _grounding_lines(context: PhotoContext) -> list[str]:
    """The optional grounding lines, in prompt order, omitting any the album
    lacks — so an album with no structure yields none and the base prompt is
    byte-for-byte the no-context one (genericity guard)."""
    candidates = (
        _segment_line(context),
        _location_line(context),
        _people_line(context),
        _meta_line(context),
    )
    return [line for line in candidates if line is not None]


def _segment_line(context: PhotoContext) -> str | None:
    if not context.segment_label:
        return None
    place = f" in {context.segment_place}" if context.segment_place else ""
    return f"Context: {context.segment_label}{place}."


def _location_line(context: PhotoContext) -> str | None:
    return f"Location: {context.photo_place}." if context.photo_place else None


def _people_line(context: PhotoContext) -> str | None:
    if not context.person_names:
        return None
    return f"People in this photo: {', '.join(context.person_names)}."


def _meta_line(context: PhotoContext) -> str | None:
    return f"Album context: {context.album_meta}." if context.album_meta else None
