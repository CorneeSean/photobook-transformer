"""A thin, typed wrapper over the Immich REST API.

This is the *only* module that talks to Immich over HTTP. The [`sync`](../stages/sync.py)
stage is the boundary that lands Immich data in our DB; everything downstream reads
from the DB, never from here.

Modelled against the Immich OpenAPI spec (v3.0.2). Notable facts pinned down while
building this (see knowledge/immich-api.md):

- The public REST API exposes **no CLIP embeddings** (resolves open question #1) —
  the ``quality`` stage computes its own via ``open_clip``.
- An album's assets are **not** on ``GET /albums/{id}`` anymore; they're listed via
  ``POST /search/metadata`` with ``albumIds``, paginated through ``nextPage``.
- Per-photo timezone *is* exposed (``exifInfo.timeZone`` + ``dateTimeOriginal``),
  which ``sync`` uses to normalize capture times to UTC.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field

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
        return cls(settings.immich_base_url, settings.immich_api_key)

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

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        """Raw thumbnail bytes for an asset, via ``GET /assets/{id}/thumbnail``."""
        response = self._client.get(f"/assets/{asset_id}/thumbnail", params={"size": size})
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
