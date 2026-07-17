"""Extract an album's ``segments`` from a source document, via a PydanticAI agent.

Feature B of knowledge/build/single-command-ingestion.md: the user points
``--source-document`` at a schedule document — a trip itinerary, a wedding
run-sheet, a festival line-up, a conference agenda — and this turns it into the
album's time/place ``segments`` instead of hand-typing them.

**Genericity is load-bearing** (folder-structure.md): the module, model, fields,
and prompt talk about *"a document describing time/place windows" → segments* —
never "itinerary"/"day"/"trip" as *the* concept. The system prompt lists mixed
example document kinds *on purpose*, precisely so the extraction stays generic
rather than assuming one occasion.

Like [`analysis.py`](./analysis.py) this module is DB-free and side-effect-free
(no ``Session``, no ``album_id``): it turns bytes/text into a validated result;
the caller ([`ingest.py`](../ingest.py)) owns reading the file, grounding the call
with the photos' capture range, and writing the result into ``album.yml``. The
loader (``AlbumYAML``) stays the source of truth for correctness — it normalizes
the emitted timestamps to UTC and enforces ordering/non-overlap — so the agent
can be a touch loose (same posture as ``analysis.py``'s bounded fields).
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model
from pypdf import PdfReader

from album.agents._model import default_model
from album.definition import SegmentYAML

_SYSTEM_PROMPT = """\
You turn a document that describes time/place windows into an ordered list of
segments for a photo book. The document might be a trip itinerary, a wedding
run-sheet, a festival line-up, or a conference agenda — treat them all the same:
find each distinct window of time (with a place, if one is stated) and emit one
segment per window, in the order they occur.

For each segment produce a short `label` (the place, or the phase of the event),
an optional `place`, and `start_at`/`end_at` as ISO 8601 timestamps — include a
UTC offset whenever you can infer one from the timezone or the capture range you
are given. Use the photo capture range and timezone to resolve relative or partial
dates ("Day 1", "Saturday", "the morning of the 2nd") into real instants. If the
document names the overall event, return it as `title`. Never invent windows the
document does not describe; if it describes none, return an empty list.
"""

_IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class SegmentExtractionError(RuntimeError):
    """The document carried no extractable text or images to work from."""


class ExtractedSegments(BaseModel):
    """The time/place windows extracted from a document, plus an optional title.

    The segment shape is the album's own :class:`~album.definition.SegmentYAML`
    (reused, not duplicated). The agent emits ISO 8601 timestamps; the album.yml
    loader (``AlbumYAML``) normalizes them to UTC and enforces ordering/non-
    overlap, so it — not this model — is the source of truth for correctness.
    """

    title: str | None = Field(
        default=None,
        description="The overall event's name, if the document states one.",
    )
    segments: list[SegmentYAML] = Field(
        default_factory=list,
        description="The extracted time/place windows, in order.",
    )


def extract_segments(
    content: str | list[BinaryContent],
    *,
    capture_range: tuple[datetime, datetime] | None = None,
    timezone: str | None = None,
    model: Model | None = None,
) -> ExtractedSegments:
    """Extract segments from already-read document ``content`` (text, or page
    images for a scanned document). Pure — no filesystem/DB. ``capture_range`` and
    ``timezone`` anchor relative dates. ``model`` defaults to real Claude; tests
    inject ``TestModel``/``FunctionModel``."""
    prompt: list[str | BinaryContent] = [_format_grounding(capture_range, timezone)]
    if isinstance(content, str):
        prompt.append(content)
    else:
        prompt.extend(content)
    result = _agent.run_sync(prompt, model=model or default_model())
    return result.output


def extract_segments_from_pdf(
    data: bytes,
    *,
    capture_range: tuple[datetime, datetime] | None = None,
    timezone: str | None = None,
    model: Model | None = None,
) -> ExtractedSegments:
    """Read a PDF's text (or, for a scanned/image-only PDF, its page images) and
    extract segments. Raises :class:`SegmentExtractionError` if the document
    yields neither."""
    text = read_pdf_text(data)
    if text:
        return extract_segments(text, capture_range=capture_range, timezone=timezone, model=model)
    images = read_pdf_images(data)
    if images:
        return extract_segments(images, capture_range=capture_range, timezone=timezone, model=model)
    raise SegmentExtractionError("no extractable text or images found in the document")


def read_pdf_text(data: bytes) -> str:
    """All text extractable from a PDF via ``pypdf`` — empty for a scanned PDF."""
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() for page in reader.pages).strip()


def read_pdf_images(data: bytes) -> list[BinaryContent]:
    """Embedded page images from a PDF — the scanned/image-only fallback, sent to
    the model the way ``analysis.py`` sends a photo thumbnail."""
    reader = PdfReader(BytesIO(data))
    return [
        BinaryContent(data=image.data, media_type=_image_media_type(image.name))
        for page in reader.pages
        for image in page.images
    ]


def _format_grounding(capture_range: tuple[datetime, datetime] | None, timezone: str | None) -> str:
    """The text half of the prompt: the task plus whatever date/zone anchors the
    caller has. Standalone and pure, so it's directly unit-testable (like
    ``analysis.format_context``)."""
    lines = ["Extract the time/place windows this document describes, as ordered segments."]
    if capture_range is not None:
        start, end = capture_range
        lines.append(
            f"The photos were captured between {start.isoformat()} and {end.isoformat()} — "
            "use this to anchor relative or partial dates.",
        )
    if timezone is not None:
        lines.append(f"Interpret local times in the {timezone} timezone unless stated otherwise.")
    lines.append("Emit ISO 8601 start_at/end_at for each window; return the event name as title.")
    return "\n".join(lines)


def _image_media_type(name: str) -> str:
    return _IMAGE_MEDIA_TYPES.get(Path(name).suffix.lower(), "image/png")


_agent = Agent(output_type=ExtractedSegments, system_prompt=_SYSTEM_PROMPT)
