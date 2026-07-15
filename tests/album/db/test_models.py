from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from album.db.models import Album, Person, Photo, Segment


class TestModels:
    """Album/Segment/Photo creation, relationships, and constraints (SQLite)."""

    def test_album_with_segments_created_and_related(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_segment: Callable[..., Segment],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="honeymoon-2026")
        segment = make_segment(album, position=1, label="Day 1")
        photo = make_photo(album, immich_asset_id="asset-1", segment=segment)
        session.add(album)
        session.commit()

        stored = session.scalar(select(Album).where(Album.slug == "honeymoon-2026"))
        assert stored is not None
        assert [s.label for s in stored.segments] == ["Day 1"]
        assert stored.photos == [photo]
        assert photo.segment is segment

    def test_album_without_segments_created_and_related(
        self, session: Session, make_album: Callable[..., Album], make_photo: Callable[..., Photo]
    ) -> None:
        album = make_album(slug="unsorted-dump", title="Unsorted Dump")
        photo = make_photo(album, immich_asset_id="asset-2")
        session.add(album)
        session.commit()

        stored = session.scalar(select(Album).where(Album.slug == "unsorted-dump"))
        assert stored is not None
        assert stored.segments == []
        assert stored.photos == [photo]
        assert photo.segment_id is None

    def test_person_unique_per_album_and_defaults(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album = make_album()
        session.add(album)
        session.commit()
        person = Person(album_id=album.id, immich_person_id="p-1", name="Alex")
        session.add(person)
        session.commit()

        # is_lead defaults to False; the same Immich person can't be cached twice
        # for one album.
        assert person.is_lead is False
        session.add(Person(album_id=album.id, immich_person_id="p-1", name="Alex again"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_photo_face_columns_default_null(
        self, session: Session, make_album: Callable[..., Album], make_photo: Callable[..., Photo]
    ) -> None:
        album = make_album()
        photo = make_photo(album, immich_asset_id="asset-9")
        session.add(album)
        session.commit()

        # NULL until `sync` has fetched faces (resumability invariant).
        assert photo.face_count is None
        assert photo.person_ids is None

    def test_segment_position_unique_per_album(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_segment: Callable[..., Segment],
    ) -> None:
        album = make_album()
        make_segment(album, position=1, label="Day 1")
        make_segment(album, position=1, label="Day 1 duplicate")
        session.add(album)

        with pytest.raises(IntegrityError):
            session.commit()
