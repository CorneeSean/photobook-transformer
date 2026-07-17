"""A thin, typed wrapper over the Immich REST API.

This is the *only* module that talks to Immich over HTTP. The [`sync`](../stages/sync.py)
stage is the boundary that lands Immich data in our DB; everything downstream reads
from the DB, never from here.

Modelled against the Immich OpenAPI spec (v3.0.2). Notable facts pinned down while
building this (see knowledge/reference/immich-api.md):

- The public REST API exposes **no CLIP embeddings** (resolves open question #1) —
  the ``quality`` stage computes its own via ``open_clip``.
- An album's assets are **not** on ``GET /albums/{id}`` anymore; they're listed via
  ``POST /search/metadata`` with ``albumIds``, paginated through ``nextPage``.
- Per-photo timezone *is* exposed (``exifInfo.timeZone`` + ``dateTimeOriginal``),
  which ``sync`` uses to normalize capture times to UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from album.config import get_settings

# Immich caps `search/metadata` page size at 1000; request the max to minimize
# round-trips when syncing a large album.
_SEARCH_PAGE_SIZE = 1000


class ImmichExif(BaseModel):
    """The subset of Immich's ``ExifResponseDto`` the pipeline consumes."""

    model_config = ConfigDict(populate_by_name=True)

    date_time_original: datetime | None = Field(default=None, alias="dateTimeOriginal")
    time_zone: str | None = Field(default=None, alias="timeZone")
    latitude: float | None = None
    longitude: float | None = None
    make: str | None = None
    model: str | None = None


class ImmichAsset(BaseModel):
    """The subset of Immich's ``AssetResponseDto`` the pipeline consumes."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    original_file_name: str = Field(alias="originalFileName")
    # Local wall-clock capture time; a fallback for when exifInfo.dateTimeOriginal
    # is absent. `sync` combines it with the zone to normalize to UTC.
    local_date_time: datetime | None = Field(default=None, alias="localDateTime")
    duplicate_id: str | None = Field(default=None, alias="duplicateId")
    exif_info: ImmichExif | None = Field(default=None, alias="exifInfo")


class ImmichDuplicate(BaseModel):
    """A duplicate group from Immich's ``DuplicateResponseDto``."""

    model_config = ConfigDict(populate_by_name=True)

    duplicate_id: str = Field(alias="duplicateId")
    assets: list[ImmichAsset]


class ImmichFace(BaseModel):
    """The subset of Immich's ``AssetFaceResponseDto`` the pipeline consumes.

    Immich nests the recognized subject under ``person`` (``null`` for a
    detected-but-unassigned face); we flatten just ``person.id``/``person.name``
    up to the boundary and drop the bounding-box/thumbnail detail — the same
    lean shape as :class:`ImmichAsset`. ``person_id`` is ``None`` for a
    detected-but-unassigned face, which still counts as a real face.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    # sourceType is machine-learning | exif | manual.
    source_type: str | None = Field(default=None, alias="sourceType")
    person_id: str | None = Field(default=None, alias="personId")
    person_name: str | None = Field(default=None, alias="personName")

    @model_validator(mode="before")
    @classmethod
    def _flatten_person(cls, data: Any) -> Any:
        """Lift ``person.id``/``person.name`` up to ``personId``/``personName``.

        ``person`` is ``null`` for a detected-but-unassigned face; leave the
        person fields at their ``None`` default in that case.
        """
        if isinstance(data, dict) and isinstance(data.get("person"), dict):
            person: dict[str, Any] = data["person"]
            return {**data, "personId": person.get("id"), "personName": person.get("name")}
        return data


class ImmichJobCounts(BaseModel):
    """The outstanding-work counts of one Immich job queue — the subset of
    ``JobCountsDto`` that :meth:`ImmichClient.get_jobs` consumes."""

    active: int = 0
    waiting: int = 0


class ImmichClient:
    """Thin typed wrapper over the Immich REST API.

    Construct with an explicit ``base_url`` + ``api_key`` (tests do this against
    ``respx``), or via :meth:`from_settings` for real runs. Usable as a context
    manager so the underlying HTTP connection pool is closed.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        # base_url includes the `/api` prefix, e.g. http://localhost:2283/api
        self._client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=30.0,
        )

    @classmethod
    def from_settings(cls) -> ImmichClient:
        settings = get_settings()
        return cls(settings.immich.base_url, settings.immich.api_key)

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]:
        """Every asset in an Immich album, following pagination to completion."""
        assets: list[ImmichAsset] = []
        page: int | None = 1
        while page is not None:
            response = self._client.post(
                "/search/metadata",
                json={"albumIds": [immich_album_id], "page": page, "size": _SEARCH_PAGE_SIZE},
            )
            response.raise_for_status()
            bucket = response.json()["assets"]
            assets.extend(ImmichAsset.model_validate(item) for item in bucket["items"])
            next_page = bucket.get("nextPage")
            page = int(next_page) if next_page is not None else None
        return assets

    def get_asset(self, asset_id: str) -> ImmichAsset:
        """A single asset with its EXIF, via ``GET /assets/{id}``."""
        response = self._client.get(f"/assets/{asset_id}")
        response.raise_for_status()
        return ImmichAsset.model_validate(response.json())

    def get_duplicates(self) -> list[ImmichDuplicate]:
        """All duplicate groups Immich has detected, via ``GET /duplicates``."""
        response = self._client.get("/duplicates")
        response.raise_for_status()
        return [ImmichDuplicate.model_validate(group) for group in response.json()]

    def upload_asset(self, path: Path) -> str:
        """Upload a local file to Immich via ``POST /assets``; return its asset id.

        Used for ``lead_faces.reference_image`` — a photo of the priority
        subjects whose Immich-detected faces name the leads (see
        knowledge/build/enrichment.md). Immich dedupes by content: re-uploading
        identical bytes returns ``status="duplicate"`` with the *existing*
        asset's id, so this is safe to retry.
        """
        stat = path.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        with path.open("rb") as handle:
            response = self._client.post(
                "/assets",
                data={
                    # deviceAssetId must be stable for the same file so Immich's
                    # own upload dedupe can recognize a repeat.
                    "deviceAssetId": f"{path.name}-{stat.st_size}",
                    "deviceId": "photobook-transformer",
                    "fileCreatedAt": created_at,
                    "fileModifiedAt": created_at,
                },
                files={"assetData": (path.name, handle, "application/octet-stream")},
            )
        response.raise_for_status()
        return response.json()["id"]

    def create_album(self, name: str, asset_ids: list[str]) -> str:
        """Create an Immich album named ``name`` seeded with ``asset_ids``; return
        its id, via ``POST /albums``.

        Immich creates the album and adds the given assets in a single call
        (``AlbumResponseDto.id``). The automated form of the manual
        [`ingest`](knowledge/stages/ingest.md) step, used by single-command
        ingestion (knowledge/build/single-command-ingestion.md) — the caller has
        already uploaded each file via :meth:`upload_asset` and collected the ids.
        """
        response = self._client.post(
            "/albums",
            json={"albumName": name, "assetIds": asset_ids},
        )
        response.raise_for_status()
        return response.json()["id"]

    def get_jobs(self) -> dict[str, ImmichJobCounts]:
        """Each job queue's outstanding-work counts keyed by queue name, via
        ``GET /jobs`` (flattening ``AllJobStatusResponseDto``'s per-queue
        ``jobCounts`` up to the boundary, dropping the unused ``queueStatus``).

        Single-command ingestion polls this after upload to wait out Immich's
        async EXIF/face/dedupe processing before running the pipeline — a queue
        is idle when its counts show no ``active``/``waiting`` work (see
        knowledge/build/single-command-ingestion.md).
        """
        response = self._client.get("/jobs")
        response.raise_for_status()
        return {
            queue: ImmichJobCounts.model_validate(status["jobCounts"])
            for queue, status in response.json().items()
        }

    def get_faces(self, asset_id: str) -> list[ImmichFace]:
        """Detected faces for an asset, via ``GET /faces?id={asset_id}``.

        Each face may or may not be assigned to a ``Person``; an unassigned face
        (``person: null``) is still a real detection with no ``person_id`` yet.
        """
        response = self._client.get("/faces", params={"id": asset_id})
        response.raise_for_status()
        return [ImmichFace.model_validate(face) for face in response.json()]

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        """Raw thumbnail bytes for an asset, via ``GET /assets/{id}/thumbnail``."""
        response = self._client.get(f"/assets/{asset_id}/thumbnail", params={"size": size})
        response.raise_for_status()
        return response.content

    def get_original(self, asset_id: str) -> bytes:
        """Raw, full-resolution original bytes for an asset, via
        ``GET /assets/{id}/original``.

        Unlike :meth:`get_thumbnail` (a small downscaled JPEG for AI analysis),
        this is the actual source pixels `render` (darktable-cli) needs — XMP
        sidecars carry only edit instructions, not image data.
        """
        response = self._client.get(f"/assets/{asset_id}/original")
        response.raise_for_status()
        return response.content

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
