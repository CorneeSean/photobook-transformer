"""Tests for :mod:`album.manifest` — parsing + validating a root ``ingest.yml``
into ``Manifest``/``AlbumSpec``/``SegmentSpec``.

Pure shape/relationship validation: no filesystem access, no Immich. Mirrors
``test_definition.py`` — a valid manifest round-trips into the expected models,
and every rejection rule gets its own test. All defaults left ``None`` here are
resolved later, at ingest time (steps 33/34).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from album.manifest import Manifest, SegmentSpec, load_manifest


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ingest.yml"
    path.write_text(body)
    return path


class TestManifest:
    """`load_manifest`: parsing, defaults, and cross-item validation."""

    def test_minimal_one_album_manifest_loads_with_none_defaults(self, tmp_path: Path) -> None:
        manifest = load_manifest(_write(tmp_path, "albums:\n  - path: lisbon-trip\n"))

        assert isinstance(manifest, Manifest)
        assert len(manifest.albums) == 1
        album = manifest.albums[0]
        assert album.path == "lisbon-trip"
        # Defaults stay None — resolved at ingest time, not here.
        assert (album.slug, album.title, album.timezone) == (None, None, None)
        assert (album.source_document, album.lead_faces) == (None, None)
        assert album.segments == []

    def test_rich_manifest_round_trips(self, tmp_path: Path) -> None:
        body = (
            "albums:\n"
            "  - path: lisbon-trip\n"
            "    slug: lisbon-2026\n"
            "    title: Lisbon 2026\n"
            "    timezone: Europe/Podgorica\n"
            "    lead_faces: [bride.jpg, groom.jpg]\n"
            "    segments:\n"
            "      - path: day-1-harbour\n"
            "      - path: day-2/old-town\n"
            "        label: Old Town\n"
            "        place: Lisbon\n"
            "  - path: wedding\n"
            "    source_document: runsheet.pdf\n"
        )

        manifest = load_manifest(_write(tmp_path, body))

        first, second = manifest.albums
        assert (first.slug, first.title, first.timezone) == (
            "lisbon-2026",
            "Lisbon 2026",
            "Europe/Podgorica",
        )
        assert first.lead_faces == ["bride.jpg", "groom.jpg"]
        assert first.segments == [
            SegmentSpec(path="day-1-harbour"),
            SegmentSpec(path="day-2/old-town", label="Old Town", place="Lisbon"),
        ]
        assert second.source_document == "runsheet.pdf"
        assert second.segments == []

    def test_empty_albums_list_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            load_manifest(_write(tmp_path, "albums: []\n"))

    @pytest.mark.parametrize(
        "album_path",
        ["/lisbon", "../lisbon", "lisbon/../../etc"],
        ids=["absolute", "leading-dotdot", "escaping-dotdot"],
    )
    def test_album_path_escaping_the_root_raises(self, tmp_path: Path, album_path: str) -> None:
        with pytest.raises(ValidationError, match="relative|escape"):
            load_manifest(_write(tmp_path, f"albums:\n  - path: {album_path}\n"))

    def test_duplicate_album_path_raises(self, tmp_path: Path) -> None:
        body = "albums:\n  - path: lisbon\n  - path: lisbon\n"
        with pytest.raises(ValidationError, match="duplicate"):
            load_manifest(_write(tmp_path, body))

    def test_colliding_resolved_slugs_raise(self, tmp_path: Path) -> None:
        # Second album's explicit slug equals the first album's defaulted slug.
        body = "albums:\n  - path: lisbon-trip\n  - path: other\n    slug: lisbon-trip\n"
        with pytest.raises(ValidationError, match="slug"):
            load_manifest(_write(tmp_path, body))

    @pytest.mark.parametrize(
        "segment_path",
        ["../day-1", "/day-1", "day-1/../../etc"],
        ids=["leading-dotdot", "absolute", "escaping-dotdot"],
    )
    def test_segment_path_escaping_its_album_raises(
        self,
        tmp_path: Path,
        segment_path: str,
    ) -> None:
        body = f"albums:\n  - path: lisbon\n    segments:\n      - path: {segment_path}\n"
        with pytest.raises(ValidationError, match="relative|escape"):
            load_manifest(_write(tmp_path, body))

    def test_nested_segment_paths_within_an_album_raise(self, tmp_path: Path) -> None:
        body = (
            "albums:\n"
            "  - path: lisbon\n"
            "    segments:\n"
            "      - path: day-1\n"
            "      - path: day-1/morning\n"
        )
        with pytest.raises(ValidationError, match="nest"):
            load_manifest(_write(tmp_path, body))

    def test_component_prefix_not_string_prefix_for_nesting(self, tmp_path: Path) -> None:
        # `day-1` is a string-prefix of `day-10` but NOT a path-component prefix,
        # so these two must be accepted (regression guard for the nesting check).
        body = (
            "albums:\n  - path: lisbon\n    segments:\n      - path: day-1\n      - path: day-10\n"
        )
        manifest = load_manifest(_write(tmp_path, body))
        assert [s.path for s in manifest.albums[0].segments] == ["day-1", "day-10"]

    def test_segments_and_source_document_are_mutually_exclusive(self, tmp_path: Path) -> None:
        body = (
            "albums:\n"
            "  - path: wedding\n"
            "    source_document: runsheet.pdf\n"
            "    segments:\n"
            "      - path: day-1\n"
        )
        with pytest.raises(ValidationError, match="mutually exclusive|source_document"):
            load_manifest(_write(tmp_path, body))

    def test_unknown_top_level_key_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            load_manifest(_write(tmp_path, "albums:\n  - path: lisbon\nextra: nope\n"))

    def test_unknown_per_album_key_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            load_manifest(_write(tmp_path, "albums:\n  - path: lisbon\n    typo: nope\n"))

    def test_unknown_per_segment_key_raises(self, tmp_path: Path) -> None:
        body = "albums:\n  - path: lisbon\n    segments:\n      - path: day-1\n        typo: x\n"
        with pytest.raises(ValidationError):
            load_manifest(_write(tmp_path, body))
