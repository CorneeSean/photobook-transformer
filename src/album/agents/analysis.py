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
from functools import cache
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from album.config import get_settings

_DEFAULT_MODEL_NAME = "claude-sonnet-5"

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
    result = _agent.run_sync(prompt, model=model or _default_model())
    return result.output


@cache
def _default_model() -> Model:
    # Cached: each AnthropicProvider owns its own httpx client / connection
    # pool, and a real run calls analyze_photo() once per selected photo
    # (hundreds per album) — rebuilding it per call would leak an unclosed
    # client and a fresh TLS handshake every time.
    provider = AnthropicProvider(api_key=get_settings().anthropic_api_key)
    return AnthropicModel(_DEFAULT_MODEL_NAME, provider=provider)


def format_context(context: PhotoContext) -> str:
    """Build the text half of the prompt from whatever grounding the album has.
    Standalone and pure so it's directly unit-testable, like ``quality.py``'s
    ``compute_blur_score``."""
    lines = ["Analyze this photo."]
    if context.segment_label:
        place = f" in {context.segment_place}" if context.segment_place else ""
        lines.append(f"Context: {context.segment_label}{place}.")
    if context.person_names:
        lines.append(f"People in this photo: {', '.join(context.person_names)}.")
    if context.album_meta:
        lines.append(f"Album context: {context.album_meta}.")
    return "\n".join(lines)
