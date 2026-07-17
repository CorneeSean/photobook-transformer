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
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import yaml

from album.immich.client import ImmichClient, ImmichJobCounts

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
    ) -> None:
        self._client = client
        self._albums_dir = albums_dir
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s
        self._sleep = sleep

    def ingest(
        self,
        directory: Path,
        *,
        slug: str | None = None,
        title: str | None = None,
        timezone: str | None = None,
        force: bool = False,
    ) -> str:
        """Ingest ``directory`` and return the album slug to run the pipeline on.

        ``slug``/``title`` override the directory-name defaults; ``timezone`` (an
        IANA name) is written through for undated photos. If the slug was already
        imported, refuse unless ``force`` — which re-runs the existing album *as
        is* (see :meth:`_reuse_existing`).
        """
        directory = directory.resolve()  # so "." picks up the real directory name
        slug = slug or _slugify(directory.name)
        album_yml = self._albums_dir / slug / "album.yml"
        if album_yml.exists():
            return self._reuse_existing(album_yml, slug, force=force)

        image_paths = self._collect_images(directory)
        asset_ids = [self._client.upload_asset(path) for path in image_paths]
        resolved_title = self._resolve_title(title, directory, slug)
        immich_album_id = self._client.create_album(resolved_title, asset_ids)

        self._wait_for_readiness()
        self._materialize(album_yml, slug, resolved_title, immich_album_id, timezone)
        _log.info(
            "ingested %d photos from %s → album %r (immich id %s)",
            len(asset_ids),
            directory,
            slug,
            immich_album_id,
        )
        return slug

    @staticmethod
    def _reuse_existing(album_yml: Path, slug: str, *, force: bool) -> str:
        """Handle an already-imported slug: refuse unless ``force``.

        Without ``force`` this is a clear error — the pipeline is load-once per
        slug, so silently re-importing would either strand a duplicate Immich
        album or leave a regenerated ``album.yml`` ignored. **With** ``force`` we
        re-run the *existing* ``album.yml`` untouched (no re-upload, no duplicate
        Immich album, and — importantly — any hand-authored ``segments`` /
        ``lead_faces`` are preserved). Re-importing genuinely new photos is out of
        scope; use ``--slug NAME`` for a distinct album.
        """
        if not force:
            raise IngestError(
                f"album {slug!r} already exists at {album_yml}; pass --force to "
                "re-run it as is, or --slug NAME to import a distinct album"
            )
        _log.info("album %r already imported; --force re-runs it (album.yml left untouched)", slug)
        return slug

    @staticmethod
    def _resolve_title(title: str | None, directory: Path, slug: str) -> str:
        """Explicit ``--title`` wins; else a title from the directory name; else
        the slug. (An EXIF date/place title is deferred — it needs Immich's
        geocoding surface, still unbuilt; see single-command-ingestion.md.)"""
        return title or _titleize(directory.name) or slug

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
        album_yml: Path, slug: str, title: str, immich_album_id: str, timezone: str | None
    ) -> None:
        """Write a minimal ``album.yml`` (the normal load path reads it next)."""
        album_yml.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {"slug": slug, "title": title, "immich_album_id": immich_album_id}
        if timezone is not None:
            data["timezone"] = timezone
        _ = album_yml.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )


def _is_image(path: Path) -> bool:
    """A file the ingestor uploads — an image with a recognized suffix."""
    return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES


def _slugify(name: str) -> str:
    """A filesystem/URL-safe album slug from a directory name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "album"


def _titleize(name: str) -> str:
    """A human title from a directory name: ``my-photos_2026`` → ``My Photos 2026``."""
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(word.capitalize() for word in words if word)


def ingest_directory(
    directory: Path,
    *,
    slug: str | None = None,
    title: str | None = None,
    timezone: str | None = None,
    force: bool = False,
) -> str:
    """Ingest ``directory`` with the real Immich client and return the slug the
    pipeline should run — the production entry point the CLI calls.

    Builds :class:`~album.immich.client.ImmichClient` from settings (closing it
    afterward); the class-based :class:`DirectoryIngestor` is what tests exercise
    with a fake client.
    """
    with ImmichClient.from_settings() as client:
        return DirectoryIngestor(client).ingest(
            directory, slug=slug, title=title, timezone=timezone, force=force
        )
