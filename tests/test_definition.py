from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from album.db.models import Album
from album.definition import load_album_definition, load_lead_faces

FIXTURES = Path(__file__).parent / "fixtures" / "albums"


class TestLoadAlbumDefinition:
    """`load_album_definition`: parsing, timezone normalization, and validation."""

    def test_loads_trip_shaped_album_with_explicit_offsets(self) -> None:
        album = load_album_definition(FIXTURES / "trip.yml")

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
        album = load_album_definition(FIXTURES / "wedding.yml")

        assert album.slug == "smith-wedding"
        assert [s.label for s in album.segments] == ["Getting ready", "Ceremony"]
        assert album.segments[1].place == "St. Mary's"

    def test_loads_segment_less_album(self) -> None:
        album = load_album_definition(FIXTURES / "no_segments.yml")

        assert album.slug == "family-2026"
        assert album.segments == []

    def test_naive_time_with_album_timezone_lands_on_same_utc_instant_as_offset(self) -> None:
        offset_based = load_album_definition(FIXTURES / "trip.yml")
        naive_based = load_album_definition(FIXTURES / "naive_with_tz.yml")

        assert naive_based.segments[0].start_at == offset_based.segments[0].start_at
        assert naive_based.segments[0].end_at == offset_based.segments[0].end_at
        assert naive_based.segments[0].start_at.tzinfo is UTC

    def test_naive_time_without_album_timezone_raises(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            load_album_definition(FIXTURES / "naive_without_tz.yml")

    def test_overlapping_segments_raise_clearly(self) -> None:
        with pytest.raises(ValidationError, match="overlap"):
            load_album_definition(FIXTURES / "overlapping.yml")

    def test_malformed_segment_raises_clearly(self) -> None:
        with pytest.raises(ValidationError, match="end_at"):
            load_album_definition(FIXTURES / "malformed.yml")

    def test_unknown_album_timezone_raises_clearly(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            load_album_definition(FIXTURES / "unknown_timezone.yml")

    def test_dst_spring_forward_gap_raises_clearly(self) -> None:
        with pytest.raises(ValidationError, match="does not exist"):
            load_album_definition(FIXTURES / "dst_gap.yml")

    def test_loaded_album_can_be_persisted(self, session: Session) -> None:
        album = load_album_definition(FIXTURES / "trip.yml")

        session.add(album)
        session.commit()

        fetched = session.query(Album).filter_by(slug="honeymoon-2026").one()
        assert len(fetched.segments) == 2


class TestLoadLeadFaces:
    """`load_lead_faces`: the optional priority-subjects block on album.yml."""

    def _write(self, tmp_path: Path, lead_block: str) -> Path:
        yml = tmp_path / "album.yml"
        yml.write_text(f"slug: s\ntitle: T\n{lead_block}")
        return yml

    def test_absent_lead_faces_is_none(self, tmp_path: Path) -> None:
        assert load_lead_faces(self._write(tmp_path, "")) is None

    def test_person_ids_form(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  person_ids: [p-alex, p-sam]\n"
        lead = load_lead_faces(self._write(tmp_path, block))
        assert lead is not None
        assert lead.person_ids == ["p-alex", "p-sam"]
        assert lead.reference_image is None

    def test_reference_image_form(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  reference_image: leads.jpg\n"
        lead = load_lead_faces(self._write(tmp_path, block))
        assert lead is not None
        assert lead.reference_image == "leads.jpg"

    def test_reference_asset_id_form(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  reference_asset_id: asset-42\n"
        lead = load_lead_faces(self._write(tmp_path, block))
        assert lead is not None
        assert lead.reference_asset_id == "asset-42"

    def test_more_than_one_input_raises(self, tmp_path: Path) -> None:
        block = "lead_faces:\n  reference_image: leads.jpg\n  person_ids: [p-alex]\n"
        with pytest.raises(ValidationError, match="exactly one"):
            load_lead_faces(self._write(tmp_path, block))

    def test_empty_lead_faces_block_raises(self, tmp_path: Path) -> None:
        # Present but names no one — a config mistake, not a no-op.
        block = "lead_faces: {}\n"
        with pytest.raises(ValidationError, match="exactly one"):
            load_lead_faces(self._write(tmp_path, block))
