"""Tests for :class:`~album.immich.paths.PathMapping` — pure host↔container path
translation for external-library ingestion (no filesystem, no network)."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from album.immich.paths import PathMapping


class TestPathMapping:
    """A bind mount ``host_root`` → ``container_root`` translates both ways and
    rejects paths outside its tree."""

    @pytest.fixture
    def mapping(self) -> PathMapping:
        return PathMapping(Path("/home/devel/photos"), PurePosixPath("/external"))

    def test_to_container_maps_under_the_mount(self, mapping: PathMapping) -> None:
        result = mapping.to_container(Path("/home/devel/photos/lisbon/img_1.jpg"))

        assert result == PurePosixPath("/external/lisbon/img_1.jpg")

    def test_to_host_is_the_inverse(self, mapping: PathMapping) -> None:
        result = mapping.to_host(PurePosixPath("/external/lisbon/img_1.jpg"))

        assert result == Path("/home/devel/photos/lisbon/img_1.jpg")

    def test_round_trips(self, mapping: PathMapping) -> None:
        host = Path("/home/devel/photos/a/b/c.heic")

        assert mapping.to_host(mapping.to_container(host)) == host

    def test_root_itself_maps_to_container_root(self, mapping: PathMapping) -> None:
        assert mapping.to_container(Path("/home/devel/photos")) == PurePosixPath("/external")

    def test_to_container_rejects_path_outside_root(self, mapping: PathMapping) -> None:
        with pytest.raises(ValueError, match="not under the external-library root"):
            mapping.to_container(Path("/etc/passwd"))

    def test_to_host_rejects_path_outside_container_root(self, mapping: PathMapping) -> None:
        with pytest.raises(ValueError, match="not under the container root"):
            mapping.to_host(PurePosixPath("/data/upload/x.jpg"))

    def test_from_settings_resolves_host_root_and_keeps_container_posix(
        self,
        tmp_path: Path,
    ) -> None:
        # Use a real directory (not a hardcoded path) so the resolve() assertion
        # is symlink-correct on any runner, and pass a trailing slash to prove it's
        # normalized away.
        mapping = PathMapping.from_settings(f"{tmp_path}/", "/external")

        assert mapping.host_root == tmp_path.resolve()
        assert mapping.container_root == PurePosixPath("/external")
        # A macOS/Windows host must not mangle the Linux container path.
        assert isinstance(mapping.container_root, PurePosixPath)
