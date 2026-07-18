"""Single-command ingestion: a local photo directory → an Immich album + a
materialized ``album.yml``, ready for the existing pipeline.

This is the automated form of the manual [`ingest`](../../knowledge/stages/ingest.md)
step and the front half of Feature A in
knowledge/build/single-command-ingestion.md. It runs **before** ``sync`` and is
*not* a pipeline stage — it owns no DAG/Prefect logic; it talks only to Immich
(via an injected client) and the filesystem, and hands the pipeline a slug.

``album.yml`` stays the **single source of truth**: this materializes one, then
the normal load path (``get_or_create_album`` → ``AlbumDefinition``) takes over,
so the generated file is inspectable/editable and there is still exactly one way
the pipeline defines an album. It introduces no occasion-specific concept — it
deals only in "a directory of images" and "an Immich album" — so the
[genericity rule](../../knowledge/reference/folder-structure.md) is unaffected.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import yaml
from pydantic_ai.models import Model

from album.agents.segments import (
    ExtractedSegments,
    SegmentExtractionError,
    extract_segments_from_pdf,
)
from album.definition import AlbumDefinition, SegmentYAML
from album.immich.client import ImmichAsset, ImmichClient, ImmichJobCounts
from album.manifest import AlbumSpec, SegmentSpec
from album.text import slugify as _slugify
from album.text import titleize as _titleize

# A pluggable "how are this album's segments computed" step, shared by both the
# PDF (`--source-document`) and the folder (manifest) paths. Given the created
# Immich album id and the {local path → asset id} map built during upload, it
# returns an optional title suggestion (the PDF path can propose one; folders
# never do) plus the segments to materialize.
SegmentSource = Callable[
    [str, dict[Path, str]],
    tuple[str | None, list[SegmentYAML] | None],
]

_log = logging.getLogger(__name__)

_ALBUMS_DIR = Path("data/albums")

# Common phone/edited photo formats Immich reliably processes (recursed into
# subdirectories). RAW is intentionally excluded — heavier to import and not the
# common case; revisit if a real run needs it.
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"})

# Immich extracts EXIF (and detects faces / dedupes) asynchronously after upload;
# `sync`/`cluster` need capture timestamps, so wait the EXIF queue out before
# running the pipeline. Mirrors LeadFaceResolver's async-detection poll shape.
_DEFAULT_POLL_INTERVAL_S = 2.0
_DEFAULT_POLL_TIMEOUT_S = 120.0
# The queue whose readiness actually gates the pipeline: metadata (EXIF)
# extraction. Faces/dedupe are best-effort and handled downstream if incomplete.
_READINESS_QUEUES = ("metadataExtraction",)


class IngestError(RuntimeError):
    """Ingestion couldn't proceed for a user-fixable reason (empty directory, a
    directory that isn't one, or a slug that's already been imported)."""


class IngestClient(Protocol):
    """The slice of :class:`~album.immich.client.ImmichClient` this module needs,
    so tests pass a fake without a network (the real client satisfies it)."""

    def upload_asset(self, path: Path) -> str: ...

    def create_album(self, name: str, asset_ids: list[str]) -> str: ...

    def get_jobs(self) -> dict[str, ImmichJobCounts]: ...

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]: ...


class DirectoryIngestor:
    """Walks a photo directory, uploads it to Immich as a new album, waits out
    Immich's async processing, and materializes an ``album.yml`` — returning the
    slug the caller hands to ``pipeline``.

    The Immich HTTP layer is injected as an :class:`IngestClient` (the real
    :class:`~album.immich.client.ImmichClient` satisfies it structurally); the
    poll interval/timeout and ``sleep`` are injected too so the readiness wait
    doesn't actually block tests — the same shape as
    :class:`~album.flow.stages.lead_faces.LeadFaceResolver`.
    """

    def __init__(
        self,
        client: IngestClient,
        *,
        albums_dir: Path = _ALBUMS_DIR,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
        model: Model | None = None,
    ) -> None:
        self._client = client
        self._albums_dir = albums_dir
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s
        self._sleep = sleep
        # PydanticAI model for the segments agent; None ⇒ real Claude. Only used
        # when a source_document is given (Feature B); tests inject a TestModel.
        self._model = model

    def ingest(
        self,
        directory: Path,
        *,
        slug: str | None = None,
        title: str | None = None,
        timezone: str | None = None,
        source_document: Path | None = None,
        force: bool = False,
        lead_face_images: list[Path] | None = None,
    ) -> str:
        """Ingest ``directory`` and return the album slug to run the pipeline on.

        ``slug``/``title`` override the directory-name defaults; ``timezone`` (an
        IANA name) is written through for undated photos. ``source_document`` (a
        PDF) fills the album's ``segments`` via the segments agent (Feature B),
        grounded on the photos' capture range. ``lead_face_images`` (from the
        ``--lead-face`` CLI flag) names the album's priority subjects — written as
        the ``lead_faces.reference_images`` block. If the slug was already imported,
        refuse unless ``force`` — which re-runs the existing album *as is* (see
        :meth:`_reuse_existing`), except that ``--lead-face`` still patches leads
        onto it; ``source_document`` is ignored on that path.
        """
        return self._ingest(
            directory,
            slug=slug,
            title=title,
            timezone=timezone,
            segment_source=self._document_source(source_document, timezone),
            force=force,
            lead_face_images=lead_face_images,
        )

    def _document_source(self, source_document: Path | None, timezone: str | None) -> SegmentSource:
        """A :data:`SegmentSource` that fills segments from a PDF via the segments
        agent (a no-op returning ``(None, None)`` when ``source_document`` is None).
        Shared by the flat :meth:`ingest` and a manifest album that names a
        ``source_document``, so both routes go through the *same* PDF path."""

        def from_document(
            immich_album_id: str,
            _path_to_asset_id: dict[Path, str],
        ) -> tuple[str | None, list[SegmentYAML] | None]:
            return self._extract_segments(immich_album_id, source_document, timezone)

        return from_document

    def ingest_album(
        self,
        spec: AlbumSpec,
        root: Path,
        *,
        timezone_default: str | None = None,
        force: bool = False,
    ) -> str:
        """Ingest one album declared by ``spec`` (relative to ``root``) and return
        its slug — the single-album unit the manifest fan-out (step 34) drives.

        The album's slug/title/timezone default to the spec's values, else the same
        directory-name defaults as :meth:`ingest` (so the resolved slug matches the
        manifest's ``effective_slug`` collision guard). ``timezone_default`` is the
        CLI ``--timezone`` global, which a spec's own ``timezone`` overrides.
        Segments come from the spec's declared subfolders (each window derived from
        its photos' capture timestamps) unless the spec names a ``source_document``,
        which routes through the same PDF path as :meth:`ingest` — the manifest
        loader guarantees the two are mutually exclusive.
        """
        album_dir = (root / spec.path).resolve()
        timezone = spec.timezone or timezone_default
        lead_face_images = (
            [album_dir / image for image in spec.lead_faces] if spec.lead_faces else None
        )
        return self._ingest(
            album_dir,
            slug=spec.slug,
            title=spec.title,
            timezone=timezone,
            segment_source=self._album_segment_source(spec, album_dir, timezone),
            force=force,
            lead_face_images=lead_face_images,
        )

    def _album_segment_source(
        self,
        spec: AlbumSpec,
        album_dir: Path,
        timezone: str | None,
    ) -> SegmentSource:
        """The segment computation for a manifest album: the folder path by
        default, or the shared PDF path when the spec names a ``source_document``."""
        if spec.source_document is not None:
            return self._document_source(album_dir / spec.source_document, timezone)

        def from_folders(
            immich_album_id: str,
            path_to_asset_id: dict[Path, str],
        ) -> tuple[str | None, list[SegmentYAML] | None]:
            asset_times = _asset_times(self._client.get_album_assets(immich_album_id))
            return None, _segments_from_folders(
                album_dir,
                spec.segments,
                path_to_asset_id,
                asset_times,
            )

        return from_folders

    def _ingest(
        self,
        directory: Path,
        *,
        slug: str | None,
        title: str | None,
        timezone: str | None,
        segment_source: SegmentSource,
        force: bool,
        lead_face_images: list[Path] | None,
    ) -> str:
        """The shared upload → create → readiness → segments → materialize spine.
        The only variant between the flat/PDF and folder paths is ``segment_source``
        (how segments are computed); everything else — the existing-slug guard, the
        upload, the Immich album, the readiness wait, materialization + validation —
        is identical."""
        directory = directory.resolve()  # so "." picks up the real directory name
        slug = slug or _slugify(directory.name)
        album_yml = self._albums_dir / slug / "album.yml"
        if album_yml.exists():
            return self._reuse_existing(
                album_yml,
                slug,
                force=force,
                lead_face_images=lead_face_images,
            )

        image_paths = self._collect_images(directory)
        # Retain the {path → asset id} map: the folder segment source needs it to
        # tie each subfolder's photos back to their Immich capture timestamps.
        path_to_asset_id = {path: self._client.upload_asset(path) for path in image_paths}
        # The Immich album is named before the document is read (extraction needs
        # the created, EXIF-ready album), so it uses the pre-document title; the
        # book's album.yml title below can still prefer a document-supplied one.
        immich_album_id = self._client.create_album(
            self._resolve_title(title, directory, slug),
            list(path_to_asset_id.values()),
        )

        self._wait_for_readiness()
        doc_title, segments = segment_source(immich_album_id, path_to_asset_id)
        final_title = self._resolve_title(title, directory, slug, doc_title=doc_title)
        self._materialize(
            album_yml,
            slug,
            final_title,
            immich_album_id,
            timezone,
            segments,
            lead_face_images,
        )
        if segments:
            self._validate_materialized(album_yml)
        _log.info(
            "ingested %d photos from %s → album %r (immich id %s)",
            len(path_to_asset_id),
            directory,
            slug,
            immich_album_id,
        )
        return slug

    def _extract_segments(
        self,
        immich_album_id: str,
        source_document: Path | None,
        timezone: str | None,
    ) -> tuple[str | None, list[SegmentYAML] | None]:
        """Run the segments agent over ``source_document`` (or a no-op if none),
        grounded on the album's photo capture range. Returns the document's
        suggested title (if any) and the extracted segments."""
        if source_document is None:
            return None, None
        capture_range = _capture_range(self._client.get_album_assets(immich_album_id))
        try:
            extracted: ExtractedSegments = extract_segments_from_pdf(
                source_document.read_bytes(),
                capture_range=capture_range,
                timezone=timezone,
                model=self._model,
            )
        except SegmentExtractionError as exc:
            raise IngestError(f"{source_document}: {exc}") from exc
        return extracted.title, extracted.segments

    @staticmethod
    def _validate_materialized(album_yml: Path) -> None:
        """Fail fast if the derived segments (from a source document *or* from
        subfolders) produced an ``album.yml`` the loader rejects (naive times with
        no timezone, overlap, bad ordering) — so the error surfaces here with
        guidance rather than deep in the pipeline after ingest already reported
        success. Runs the same loader the pipeline does (``AlbumDefinition``), so it
        re-uses the one source of truth for segment correctness rather than
        re-implementing the checks; the loader's ``{exc}`` names the offending
        segments."""
        try:
            _ = AlbumDefinition().load(album_yml)
        except ValueError as exc:
            raise IngestError(
                f"the derived segments are invalid ({exc}); edit {album_yml} to fix "
                "them and re-run with --force, or import without segments",
            ) from exc

    @staticmethod
    def _reuse_existing(
        album_yml: Path,
        slug: str,
        *,
        force: bool,
        lead_face_images: list[Path] | None = None,
    ) -> str:
        """Handle an already-imported slug: refuse unless ``force``.

        Without ``force`` this is a clear error — the pipeline is load-once per
        slug, so silently re-importing would either strand a duplicate Immich
        album or leave a regenerated ``album.yml`` ignored. **With** ``force`` we
        re-run the *existing* ``album.yml`` (no re-upload, no duplicate Immich
        album) — leaving any hand-authored ``segments`` / ``lead_faces`` intact,
        *except* that ``--lead-face`` explicitly patches the ``lead_faces`` block
        (so ``generate --force --lead-face`` adds/replaces leads without
        hand-editing YAML, mirroring ``run --lead-face``). Re-importing genuinely
        new photos is out of scope; use ``--slug NAME`` for a distinct album.
        """
        if not force:
            raise IngestError(
                f"album {slug!r} already exists at {album_yml}; pass --force to "
                "re-run it as is, or --slug NAME to import a distinct album",
            )
        if lead_face_images:
            patch_lead_faces(album_yml, lead_face_images)
            _log.info("album %r already imported; --force re-runs it, --lead-face patched", slug)
        else:
            _log.info(
                "album %r already imported; --force re-runs it (album.yml left untouched)",
                slug,
            )
        return slug

    @staticmethod
    def _resolve_title(
        title: str | None,
        directory: Path,
        slug: str,
        *,
        doc_title: str | None = None,
    ) -> str:
        """Precedence: explicit ``--title`` > the source document's title >
        directory name > slug. (An EXIF date/place title is deferred — it needs
        Immich's geocoding surface, still unbuilt; see single-command-ingestion.md.)"""
        return title or doc_title or _titleize(directory.name) or slug

    def _collect_images(self, directory: Path) -> list[Path]:
        """Every image file under ``directory`` (recursing subdirectories), sorted
        for a stable order. Raises :class:`IngestError` if there are none."""
        if not directory.is_dir():
            raise IngestError(f"{directory} is not a directory")
        paths = sorted(path for path in directory.rglob("*") if _is_image(path))
        if not paths:
            suffixes = ", ".join(sorted(_IMAGE_SUFFIXES))
            raise IngestError(f"no images found in {directory} (looked for: {suffixes})")
        return paths

    def _wait_for_readiness(self) -> None:
        """Poll Immich's job queues until EXIF extraction is idle, so ``sync``/
        ``cluster`` see capture timestamps. On timeout, warn and continue rather
        than hang — undated photos are handled downstream (``sync`` flags them,
        ``cluster`` has a NULL-timestamp path). Uses the injected ``sleep`` so
        tests don't actually wait."""
        waited = 0.0
        idle = self._queues_idle()
        while not idle and waited < self._poll_timeout_s:
            self._sleep(self._poll_interval_s)
            waited += self._poll_interval_s
            idle = self._queues_idle()
        if not idle:
            _log.warning(
                "Immich still processing after %gs (queues: %s); continuing — "
                "undated photos are handled downstream (sync flags them, cluster "
                "has a NULL-timestamp path)",
                self._poll_timeout_s,
                ", ".join(_READINESS_QUEUES),
            )

    def _queues_idle(self) -> bool:
        """True when none of the readiness queues has outstanding work."""
        jobs = self._client.get_jobs()
        return all(self._queue_idle(jobs.get(queue)) for queue in _READINESS_QUEUES)

    @staticmethod
    def _queue_idle(counts: ImmichJobCounts | None) -> bool:
        # A queue Immich doesn't report is treated as idle — nothing to wait on.
        return counts is None or (counts.active == 0 and counts.waiting == 0)

    @staticmethod
    def _materialize(
        album_yml: Path,
        slug: str,
        title: str,
        immich_album_id: str,
        timezone: str | None,
        segments: list[SegmentYAML] | None = None,
        lead_face_images: list[Path] | None = None,
    ) -> None:
        """Write a minimal ``album.yml`` (the normal load path reads it next).

        ``segments`` (from :func:`~album.agents.segments.extract_segments_from_pdf`)
        are written as ISO timestamps; the loader (``AlbumYAML``) normalizes them
        to UTC and enforces ordering/non-overlap when it reads the file back.
        ``lead_face_images`` (from ``--lead-face``) are written as the
        ``lead_faces.reference_images`` block."""
        album_yml.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {
            "slug": slug,
            "title": title,
            "immich_album_id": immich_album_id,
        }
        if timezone is not None:
            data["timezone"] = timezone
        if segments:
            # mode="json" gives ISO-string timestamps; exclude_none drops an absent
            # `place` — a plain, YAML-friendly dict the loader reads straight back.
            data["segments"] = [s.model_dump(mode="json", exclude_none=True) for s in segments]
        if lead_face_images:
            data["lead_faces"] = _lead_faces_block(lead_face_images)
        _write_album_yml(album_yml, data)


def _write_album_yml(album_yml: Path, data: dict[str, object]) -> None:
    """Serialize ``data`` to ``album_yml`` — the one place the YAML dump options
    (unsorted keys so the shape reads top-down, unicode preserved) are set."""
    _ = album_yml.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _lead_faces_block(images: list[Path]) -> dict[str, object]:
    """The ``lead_faces`` YAML for CLI-supplied reference photos. Absolute paths so
    they resolve correctly regardless of the album.yml's directory (``reference_dir``
    in the resolver): ``pathlib``'s ``/`` discards the left operand when the right
    is absolute."""
    return {"reference_images": [str(path.resolve()) for path in images]}


def patch_lead_faces(album_yml: Path, images: list[Path]) -> None:
    """Overwrite an existing ``album.yml``'s ``lead_faces`` block in place — used by
    ``run --lead-face`` and ``generate --force --lead-face`` to add/replace leads
    without hand-editing YAML. Replaces the whole block (does not merge)."""
    if not album_yml.is_file():
        raise IngestError(f"no album.yml at {album_yml} — run `generate` first")
    data = yaml.safe_load(album_yml.read_text())
    if not isinstance(data, dict):
        # A present-but-empty (or non-mapping) album.yml — patching it would strip
        # slug/title and yield a file the loader rejects with a cryptic error.
        raise IngestError(f"album.yml at {album_yml} is empty or malformed — run `generate` first")
    data["lead_faces"] = _lead_faces_block(images)
    _write_album_yml(album_yml, data)


def _segments_from_folders(
    album_dir: Path,
    specs: list[SegmentSpec],
    path_to_asset_id: dict[Path, str],
    asset_times: dict[str, datetime],
) -> list[SegmentYAML]:
    """One :class:`~album.definition.SegmentYAML` per spec whose subfolder holds
    dated photos, its window ``[min, max]`` of those photos' capture instants and
    its label defaulted from the folder name. A spec whose folder has no images (or
    none with a timestamp) yields no window and is skipped with a warning — the
    album still materializes (segments are optional). Sorted by ``start_at`` so
    ``Segment.position`` ends up chronological."""
    segments: list[SegmentYAML] = []
    for spec in specs:
        folder = album_dir / spec.path
        window = _folder_time_range(_folder_times(folder, path_to_asset_id, asset_times))
        if window is None:
            _log.warning("segment folder %s has no dated photos; skipping it", folder)
            continue
        segments.append(_folder_segment(spec, window))
    return sorted(segments, key=lambda segment: segment.start_at)


def _folder_times(
    folder: Path,
    path_to_asset_id: dict[Path, str],
    asset_times: dict[str, datetime],
) -> list[datetime]:
    """The capture instants of the uploaded photos that live under ``folder``."""
    return [
        asset_times[asset_id]
        for path, asset_id in path_to_asset_id.items()
        if path.is_relative_to(folder) and asset_id in asset_times
    ]


def _folder_segment(spec: SegmentSpec, window: tuple[datetime, datetime]) -> SegmentYAML:
    """A :class:`~album.definition.SegmentYAML` for one folder spec + its window;
    the label falls back to the titleized folder name."""
    start_at, end_at = window
    return SegmentYAML(
        label=spec.label or _titleize(Path(spec.path).name),
        place=spec.place,
        start_at=start_at,
        end_at=end_at,
    )


def _folder_time_range(times: list[datetime]) -> tuple[datetime, datetime] | None:
    """``[min, max]`` of a folder's capture instants, or ``None`` if it has none. A
    single-instant folder (``max == min``) is widened to ``end = min + 1s`` so the
    ``AlbumYAML`` loader's ``end_at > start_at`` check passes."""
    if not times:
        return None
    lo, hi = min(times), max(times)
    return (lo, hi if hi > lo else lo + timedelta(seconds=1))


def _asset_times(assets: list[ImmichAsset]) -> dict[str, datetime]:
    """``{asset id → capture instant}`` for the assets that carry a timestamp, from
    the same EXIF/local-time source :func:`_capture_range` uses (naive coerced to
    UTC). The folder segment source joins this to the upload's ``{path → asset id}``
    map to window each subfolder."""
    return {
        asset.id: _as_aware(time) for asset in assets if (time := _asset_time(asset)) is not None
    }


def _capture_range(assets: list[ImmichAsset]) -> tuple[datetime, datetime] | None:
    """The (earliest, latest) capture instant across an album's assets, or ``None``
    if none carry a timestamp — the anchor the segments agent uses for relative
    dates. Naive timestamps are treated as UTC so a mix of aware/naive values
    (Immich can emit either) stays comparable; this range is only a hint."""
    times = [_as_aware(time) for asset in assets if (time := _asset_time(asset)) is not None]
    if not times:
        return None
    return min(times), max(times)


def _asset_time(asset: ImmichAsset) -> datetime | None:
    """An asset's capture instant: EXIF ``dateTimeOriginal`` if present, else the
    local wall-clock ``localDateTime`` fallback."""
    if asset.exif_info is not None and asset.exif_info.date_time_original is not None:
        return asset.exif_info.date_time_original
    return asset.local_date_time


def _as_aware(dt: datetime) -> datetime:
    """A naive datetime coerced to UTC (for the best-effort capture-range hint)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _is_image(path: Path) -> bool:
    """A file the ingestor uploads — an image with a recognized suffix."""
    return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES


def ingest_directory(
    directory: Path,
    *,
    slug: str | None = None,
    title: str | None = None,
    timezone: str | None = None,
    source_document: Path | None = None,
    force: bool = False,
    lead_face_images: list[Path] | None = None,
) -> str:
    """Ingest ``directory`` with the real Immich client and return the slug the
    pipeline should run — the production entry point the CLI calls.

    Builds :class:`~album.immich.client.ImmichClient` from settings (closing it
    afterward); the class-based :class:`DirectoryIngestor` is what tests exercise
    with a fake client. The segments agent uses real Claude here (no ``model``
    injected).
    """
    with ImmichClient.from_settings() as client:
        return DirectoryIngestor(client).ingest(
            directory,
            slug=slug,
            title=title,
            timezone=timezone,
            source_document=source_document,
            force=force,
            lead_face_images=lead_face_images,
        )
