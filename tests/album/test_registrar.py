"""Tests for the :class:`~album.ingest.AssetRegistrar` seam (build step 37) — the
one place ``external`` (scan-in-place) ingest differs from the default ``upload``
mode. The registrars are exercised directly over in-memory client fakes (no
network); the ingestor-level integration is covered in ``test_ingest.py``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import cast

import pytest

from album.config import ImmichSettings
from album.immich.client import ImmichAsset, ImmichClient, ImmichJobCounts, ImmichLibrary
from album.immich.paths import PathMapping
from album.ingest import (
    ExternalLibraryRegistrar,
    IngestError,
    UploadRegistrar,
    build_registrar,
)


class FakeUploader:
    """Records each ``upload_asset`` and hands back a stable asset id."""

    def __init__(self) -> None:
        self.uploaded: list[Path] = []

    def upload_asset(self, path: Path) -> str:
        self.uploaded.append(path)
        return f"asset-{len(self.uploaded)}"


class FakeLibraryClient:
    """In-memory Immich External-Library client: serves canned scanned ``assets``,
    tracks scans/creates, and (like Immich) drains its ``library`` job queue over a
    sequence of ``get_jobs`` responses."""

    def __init__(
        self,
        *,
        assets: list[ImmichAsset],
        libraries: list[ImmichLibrary] | None = None,
        jobs: list[dict[str, ImmichJobCounts]] | None = None,
        user_id: str = "admin-1",
    ) -> None:
        self._assets = assets
        self._libraries = list(libraries) if libraries else []
        self._jobs = list(jobs) if jobs is not None else None
        self._user_id = user_id
        self.scanned: list[str] = []
        self.created: list[tuple[str, list[str], str]] = []

    def get_current_user_id(self) -> str:
        return self._user_id

    def get_libraries(self) -> list[ImmichLibrary]:
        return list(self._libraries)

    def create_library(self, name: str, import_paths: list[str], *, owner_id: str) -> ImmichLibrary:
        library = ImmichLibrary(
            id=f"lib-{len(self._libraries) + 1}",
            name=name,
            importPaths=list(import_paths),
        )
        self._libraries.append(library)
        self.created.append((name, list(import_paths), owner_id))
        return library

    def scan_library(self, library_id: str) -> None:
        self.scanned.append(library_id)

    def get_library_assets(self, library_id: str) -> list[ImmichAsset]:
        return list(self._assets)

    def get_jobs(self) -> dict[str, ImmichJobCounts]:
        if self._jobs is None:
            return {}
        return self._jobs.pop(0) if len(self._jobs) > 1 else self._jobs[0]


_MAPPING = PathMapping(host_root=Path("/photos"), container_root=PurePosixPath("/external"))


def _asset(asset_id: str, container_path: str) -> ImmichAsset:
    """A scanned external asset carrying the container-side ``originalPath``."""
    return ImmichAsset(
        id=asset_id,
        originalFileName=PurePosixPath(container_path).name,
        originalPath=container_path,
    )


def _registrar(client: FakeLibraryClient) -> ExternalLibraryRegistrar:
    # No-op sleep + tiny timeout so the scan-queue poll never actually waits.
    return ExternalLibraryRegistrar(
        client,
        _MAPPING,
        poll_interval_s=1.0,
        poll_timeout_s=3.0,
        sleep=lambda _s: None,
    )


class TestUploadRegistrar:
    def test_uploads_each_path_and_returns_the_asset_map(self) -> None:
        client = FakeUploader()
        paths = [Path("/photos/lisbon/a.jpg"), Path("/photos/lisbon/b.jpg")]

        result = UploadRegistrar(client).register(Path("/photos/lisbon"), paths)

        assert client.uploaded == paths
        assert result == {paths[0]: "asset-1", paths[1]: "asset-2"}


class TestExternalLibraryRegistrar:
    def test_creates_scans_and_maps_by_original_path(self) -> None:
        assets = [
            _asset("ext-1", "/external/lisbon/a.jpg"),
            _asset("ext-2", "/external/lisbon/b.jpg"),
        ]
        client = FakeLibraryClient(assets=assets)
        paths = [Path("/photos/lisbon/a.jpg"), Path("/photos/lisbon/b.jpg")]

        result = _registrar(client).register(Path("/photos/lisbon"), paths)

        # Library created under the admin, over the container mount root, then scanned.
        assert client.created == [("photobook-external", ["/external"], "admin-1")]
        assert client.scanned == ["lib-1"]
        # Each host path mapped back to its scanned asset id.
        assert result == {paths[0]: "ext-1", paths[1]: "ext-2"}

    def test_reuses_an_existing_library_matched_by_import_path(self) -> None:
        existing = ImmichLibrary(id="lib-99", name="prior", importPaths=["/external"])
        client = FakeLibraryClient(
            assets=[_asset("ext-1", "/external/lisbon/a.jpg")],
            libraries=[existing],
        )

        result = _registrar(client).register(Path("/photos/lisbon"), [Path("/photos/lisbon/a.jpg")])

        assert client.created == []  # reused, not recreated
        assert client.scanned == ["lib-99"]
        assert result == {Path("/photos/lisbon/a.jpg"): "ext-1"}

    def test_waits_for_the_library_scan_queue_to_drain(self) -> None:
        # Busy, busy, then idle — the poll must not map until the queue drains.
        jobs = [
            {"library": ImmichJobCounts(active=1)},
            {"library": ImmichJobCounts(waiting=2)},
            {},
        ]
        client = FakeLibraryClient(assets=[_asset("ext-1", "/external/lisbon/a.jpg")], jobs=jobs)

        result = _registrar(client).register(Path("/photos/lisbon"), [Path("/photos/lisbon/a.jpg")])

        assert result == {Path("/photos/lisbon/a.jpg"): "ext-1"}
        assert client._jobs == [{}]  # drained down to the idle response

    def test_filters_library_assets_down_to_this_albums_paths(self) -> None:
        # The shared library spans the whole tree; another album's asset is ignored.
        assets = [
            _asset("ext-1", "/external/lisbon/a.jpg"),
            _asset("ext-other", "/external/split/z.jpg"),
        ]
        client = FakeLibraryClient(assets=assets)

        result = _registrar(client).register(Path("/photos/lisbon"), [Path("/photos/lisbon/a.jpg")])

        assert result == {Path("/photos/lisbon/a.jpg"): "ext-1"}

    def test_raises_when_an_expected_photo_was_not_scanned(self) -> None:
        client = FakeLibraryClient(assets=[_asset("ext-1", "/external/lisbon/a.jpg")])
        paths = [Path("/photos/lisbon/a.jpg"), Path("/photos/lisbon/missing.jpg")]

        with pytest.raises(IngestError, match="did not return 1 expected"):
            _ = _registrar(client).register(Path("/photos/lisbon"), paths)

    def test_raises_when_a_photo_is_outside_the_host_root(self) -> None:
        client = FakeLibraryClient(assets=[])

        with pytest.raises(IngestError, match="not under the external-library root"):
            _ = _registrar(client).register(Path("/elsewhere"), [Path("/elsewhere/a.jpg")])


def _immich(**overrides: object) -> ImmichSettings:
    return ImmichSettings(**overrides)  # pyright: ignore[reportArgumentType]


class TestBuildRegistrar:
    # `build_registrar` only stores the client on the registrar (never calls it
    # here), so a bare object stands in for the real ImmichClient in these tests.
    _CLIENT = cast(ImmichClient, object())

    def test_upload_mode_builds_an_upload_registrar(self) -> None:
        registrar = build_registrar(self._CLIENT, _immich(ingest_mode="upload"))
        assert isinstance(registrar, UploadRegistrar)

    def test_external_mode_builds_an_external_registrar(self) -> None:
        registrar = build_registrar(
            self._CLIENT,
            _immich(ingest_mode="external", external_host_root="/photos"),
        )
        assert isinstance(registrar, ExternalLibraryRegistrar)

    def test_external_mode_without_host_root_is_a_clean_error(self) -> None:
        with pytest.raises(IngestError, match="IMMICH__EXTERNAL_HOST_ROOT"):
            _ = build_registrar(self._CLIENT, _immich(ingest_mode="external"))

    def test_unknown_mode_is_a_clean_error(self) -> None:
        with pytest.raises(IngestError, match="unknown ingest mode"):
            _ = build_registrar(self._CLIENT, _immich(ingest_mode="sideways"))
