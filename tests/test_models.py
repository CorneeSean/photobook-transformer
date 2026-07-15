from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from album.db.models import Album, Photo, Segment


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
