from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from album.db.models import Album
from album.definition import AlbumDefinition, LeadFacesDefinition

FIXTURES = Path(__file__).parent.parent / "fixtures" / "albums"


class TestAlbumDefinition:
    """`AlbumDefinition.load`: parsing, timezone normalization, and validation."""

    def test_loads_trip_shaped_album_with_explicit_offsets(self) -> None:
        album = AlbumDefinition().load(FIXTURES / "trip.yml")

        assert album.slug == "honeymoon-2026"
        assert album.title == "Our Honeymoon"
        assert album.immich_album_id == "8f2c3a"
        assert album.timezone == "Europe/Podgorica"
        assert album.meta == {"occasion": "honeymoon", "names": ["Alex", "Sam"]}

        assert [s.position for s in album.segments] == [1, 2]
        assert [s.label for s in album.segments] == ["Day 1", "Day 2"]
        assert album.segments[0].place == "Dubrovnik"
        assert album.segments[0].start_at == datetime(2026, 5, 1, 22, 0, 0, tzinfo=UTC)
        assert album.segments[0].end_at == datetime(2026, 5, 2, 21, 59, 59, tzinfo=UTC)

    def test_loads_wedding_shaped_album(self) -> None:
        album = AlbumDefinition().load(FIXTURES / "wedding.yml")

        assert album.slug == "smith-wedding"
        assert [s.label for s in album.segments] == ["Getting ready", "Ceremony"]
        assert album.segments[1].place == "St. Mary's"

    def test_loads_segment_less_album(self) -> None:
        album = AlbumDefinition().load(FIXTURES / "no_segments.yml")

        assert album.slug == "family-2026"
        assert album.segments == []

    def test_naive_time_with_album_timezone_lands_on_same_utc_instant_as_offset(self) -> None:
        offset_based = AlbumDefinition().load(FIXTURES / "trip.yml")
        naive_based = AlbumDefinition().load(FIXTURES / "naive_with_tz.yml")

        assert naive_based.segments[0].start_at == offset_based.segments[0].start_at
        assert naive_based.segments[0].end_at == offset_based.segments[0].end_at
        assert naive_based.segments[0].start_at.tzinfo is UTC

    def test_naive_time_without_album_timezone_raises(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            AlbumDefinition().load(FIXTURES / "naive_without_tz.yml")

    def test_overlapping_segments_raise_clearly(self) -> None:
        with pytest.raises(ValidationError, match="overlap"):
            AlbumDefinition().load(FIXTURES / "overlapping.yml")

    def test_malformed_segment_raises_clearly(self) -> None:
        with pytest.raises(ValidationError, match="end_at"):
            AlbumDefinition().load(FIXTURES / "malformed.yml")

    def test_unknown_album_timezone_raises_clearly(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            AlbumDefinition().load(FIXTURES / "unknown_timezone.yml")

    def test_dst_spring_forward_gap_raises_clearly(self) -> None:
        with pytest.raises(ValidationError, match="does not exist"):
            AlbumDefinition().load(FIXTURES / "dst_gap.yml")

    def test_similarity_threshold_is_parsed_onto_the_album(self, tmp_path: Path) -> None:
        yml = tmp_path / "album.yml"
        yml.write_text(
            "slug: sesja\ntitle: Sesja\nimmich_album_id: abc\nsimilarity_threshold: 0.82\n",
        )

        album = AlbumDefinition().load(yml)

        assert album.similarity_threshold == 0.82

    def test_absent_similarity_threshold_defaults_to_none(self) -> None:
        # A segment-less album omits the field entirely — it must load as NULL so
        # `select` falls back to its stage default (genericity: the knob is opt-in).
        album = AlbumDefinition().load(FIXTURES / "no_segments.yml")

        assert album.similarity_threshold is None

    def test_out_of_range_similarity_threshold_raises(self, tmp_path: Path) -> None:
        yml = tmp_path / "album.yml"
        yml.write_text("slug: sesja\ntitle: Sesja\nsimilarity_threshold: 1.5\n")

        with pytest.raises(ValidationError, match="similarity_threshold"):
            AlbumDefinition().load(yml)

    def test_loaded_album_can_be_persisted(self, session: Session) -> None:
        album = AlbumDefinition().load(FIXTURES / "trip.yml")

        session.add(album)
        session.commit()

        fetched = session.query(Album).filter_by(slug="honeymoon-2026").one()
        assert len(fetched.segments) == 2


class TestLeadFacesDefinition:
    """`LeadFacesDefinition.load`: the optional priority-subjects block on album.yml."""

    def _write(self, tmp_path: Path, lead_block: str) -> Path:
        yml = tmp_path / "album.yml"
        yml.write_text(f"slug: s\ntitle: T\n{lead_block}")
        return yml

    def test_absent_lead_faces_is_none(self, tmp_path: Path) -> None:
        assert LeadFacesDefinition().load(self._write(tmp_path, "")) is None

    def test_person_ids_form(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  person_ids: [p-alex, p-sam]\n"
        lead = LeadFacesDefinition().load(self._write(tmp_path, block))
        assert lead is not None
        assert lead.person_ids == ["p-alex", "p-sam"]
        assert lead.reference_images is None

    def test_reference_images_form(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  reference_images: [alex.jpg, sam.jpg]\n"
        lead = LeadFacesDefinition().load(self._write(tmp_path, block))
        assert lead is not None
        assert lead.reference_images == ["alex.jpg", "sam.jpg"]
        assert lead.reference_asset_ids is None

    def test_single_reference_image_is_just_a_one_element_list(self, tmp_path: Path) -> None:
        # Naming one lead is the one-element case — no separate single-item form.
        block = "lead_faces:\n  reference_images: [leads.jpg]\n"
        lead = LeadFacesDefinition().load(self._write(tmp_path, block))
        assert lead is not None
        assert lead.reference_images == ["leads.jpg"]

    def test_reference_asset_ids_form(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  reference_asset_ids: [asset-42, asset-7]\n"
        lead = LeadFacesDefinition().load(self._write(tmp_path, block))
        assert lead is not None
        assert lead.reference_asset_ids == ["asset-42", "asset-7"]

    def test_more_than_one_input_raises(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  reference_images: [leads.jpg]\n  person_ids: [p-alex]\n"
        with pytest.raises(ValidationError, match="exactly one"):
            LeadFacesDefinition().load(self._write(tmp_path, block))

    def test_reference_images_plus_asset_ids_raises(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  reference_images: [alex.jpg]\n  reference_asset_ids: [asset-1]\n"
        with pytest.raises(ValidationError, match="exactly one"):
            LeadFacesDefinition().load(self._write(tmp_path, block))

    def test_empty_lead_faces_block_raises(self, tmp_path: Path) -> None:
        # Present but names no one — a config mistake, not a no-op.
        block = "lead_faces: {}\n"
        with pytest.raises(ValidationError, match="exactly one"):
            LeadFacesDefinition().load(self._write(tmp_path, block))
