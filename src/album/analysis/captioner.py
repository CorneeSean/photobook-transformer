"""``Captioner`` — a photo's caption, behind a substitutable, per-run-selectable seam.

Step 29 splits the old fused vision call in two; this is the caption half. Two
implementations sit behind one ``Captioner`` protocol (quality-practices.md
§1.6.2 Strategy, like ``Printer``):

- :class:`TemplateCaptioner` — the **offline default**. Composes who/where/when from
  the structured grounding already in the DB (names, place, capture time), no model
  and no network. Most of a photo-book caption's value is that grounding, and it
  makes a full run possible with zero external API (local-analysis.md).
- :class:`ClaudeCaptioner` — the **opt-in "rich caption"** upgrade (like
  ``LuluPrinter``): a caption-only Claude call that can also read pixel-derived scene
  semantics. Injectable ``model`` so tests use ``TestModel``/``FunctionModel``.

:func:`build_captioner` resolves a name to a concrete captioner, so the captioner is
selectable per run (``CAPTIONER`` env / ``--captioner`` flag) for A/B comparison
without code changes. A local VLM would slot in as one more registry entry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model

from album.agents._model import default_model
from album.analysis.context import PhotoContext, format_context, format_when

_GENERIC_CAPTION = "A moment worth remembering."


# runtime_checkable: ``flow/_flow.py``'s ``pipeline`` (@flow) takes a ``Captioner``
# in its signature, and Prefect builds a Pydantic parameter schema for that signature
# (even with validate_parameters=False), which runs ``isinstance`` on each annotation
# — same reason ``SimilaritySearch`` is runtime_checkable (quality-practices.md §1.4).
@runtime_checkable
class Captioner(Protocol):
    def caption(self, image: bytes, context: PhotoContext) -> str:
        """A short, natural, non-empty caption for one photo's thumbnail."""
        ...


class TemplateCaptioner:
    """Offline caption from the album's structured grounding — no model, no network."""

    def caption(self, image: bytes, context: PhotoContext) -> str:
        subject = _join_names(context.person_names)
        detail = _detail(context)
        return _compose(subject, detail, context)


def _join_names(names: list[str]) -> str | None:
    """People as ``"A"`` / ``"A and B"`` / ``"A, B and C"``; ``None`` if there are none."""
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _detail(context: PhotoContext) -> str:
    """The where/when clause: activity, location (photo place preferred over the
    segment's), then the month — exact repeats deduped, order preserved."""
    location = context.photo_place or context.segment_place
    when = format_when(context.capture_time) if context.capture_time else None
    parts = dict.fromkeys(part for part in (context.segment_label, location, when) if part)
    return ", ".join(parts)


def _compose(subject: str | None, detail: str, context: PhotoContext) -> str:
    """Join the who and the where/when, degrading gracefully to whichever is present,
    and never returning an empty (or apologetic) caption."""
    if subject and detail:
        return f"{subject} — {detail}"
    return subject or detail or _fallback(context)


def _fallback(context: PhotoContext) -> str:
    """No grounding at all — a short, accurate, non-apologetic caption, using the
    album's occasion when it has one so it still reads for any album shape."""
    occasion = context.album_meta.get("occasion")
    if isinstance(occasion, str) and occasion:
        return f"A moment from the {occasion}."
    return _GENERIC_CAPTION


_CAPTION_SYSTEM_PROMPT = """\
You are a photo-book curation assistant. Given one photo, write a short, natural
caption. Ground it in whatever context you're given (a segment name/place, the
names of people in the photo, when it was taken, album metadata) when present. When
names are given, use them naturally; never invent a name, and never state that you
don't know who someone is. If no context is given, still write an accurate, general
caption from the image alone — never mention that context is missing.
"""

_caption_agent = Agent(output_type=str, system_prompt=_CAPTION_SYSTEM_PROMPT)


class ClaudeCaptioner:
    """Opt-in rich caption via a caption-only Claude call.

    ``model`` defaults to real Claude (built inside ``default_model`` from
    ``config.py``); tests inject PydanticAI's ``TestModel``/``FunctionModel``.
    """

    def __init__(self, *, model: Model | None = None) -> None:
        self._model = model

    @classmethod
    def from_settings(cls) -> ClaudeCaptioner:
        """The production captioner. Building it never requires ``ANTHROPIC_API_KEY``
        — only a real ``caption`` call does (the key is read lazily by
        ``default_model``), so the registry can construct it unconditionally."""
        return cls()

    def caption(self, image: bytes, context: PhotoContext) -> str:
        prompt = [format_context(context), BinaryContent(data=image, media_type="image/jpeg")]
        result = _caption_agent.run_sync(prompt, model=self._model or default_model())
        caption = result.output.strip()
        if not caption:
            # Preserve the Captioner "non-empty" contract the retired AnalysisResult
            # enforced via Field(min_length=1); a blank model response is a per-photo
            # failure the caller isolates, not a silent empty caption.
            raise ValueError("model returned an empty caption")
        return caption


# name -> zero-arg factory for per-run selection (CAPTIONER env / --captioner flag).
# A local VLM would slot in as one more entry here — no other code changes.
_CAPTIONERS: dict[str, Callable[[], Captioner]] = {
    "template": TemplateCaptioner,
    "claude": ClaudeCaptioner.from_settings,
}


def build_captioner(name: str) -> Captioner:
    """Resolve a captioner name to a concrete :class:`Captioner`, or raise a clear
    error naming the valid choices."""
    try:
        factory = _CAPTIONERS[name]
    except KeyError:
        raise ValueError(f"unknown captioner {name!r}; choose from {captioner_names()}") from None
    return factory()


def captioner_names() -> list[str]:
    """The selectable captioner names, sorted — the source for the CLI's ``choices``."""
    return sorted(_CAPTIONERS)


__all__ = [
    "Captioner",
    "ClaudeCaptioner",
    "TemplateCaptioner",
    "build_captioner",
    "captioner_names",
]
