"""Tests for the shared read queries (`db/queries.py`, open question 7)."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.db.queries import selected_photos


class TestSelectedPhotos:
    """`selected_photos` — the one "selected photos, ordered by id" read shared
    by caption/enhance/render (SQLite)."""

    def test_returns_only_selected_ordered_by_id(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="album-a")
        session.add(album)
        session.commit()
        selected_first = make_photo(album, immich_asset_id="a1", selected=True)
        not_selected = make_photo(album, immich_asset_id="a2", selected=False)
        selected_second = make_photo(album, immich_asset_id="a3", selected=True)
        session.add_all([selected_first, not_selected, selected_second])
        session.commit()

        result = selected_photos(album.id, session)

        # Asserts the selected-only filter and the ascending-id contract. Note the
        # ordering half can't independently fail on SQLite: rowid == the integer PK,
        # so a bare SELECT already returns rows in id order whether or not
        # ``.order_by(Photo.id)`` is present — there's no way to make natural order
        # diverge from id order here. The clause is a visible one-liner in
        # ``selected_photos``; this documents the determinism contract rather than
        # fully guarding it.
        assert [p.id for p in result] == [selected_first.id, selected_second.id]

    def test_scoped_to_the_given_album(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album_a = make_album(slug="album-a")
        album_b = make_album(slug="album-b")
        session.add_all([album_a, album_b])
        session.commit()
        in_a = make_photo(album_a, immich_asset_id="a1", selected=True)
        in_b = make_photo(album_b, immich_asset_id="b1", selected=True)
        session.add_all([in_a, in_b])
        session.commit()

        assert selected_photos(album_a.id, session) == [in_a]

    def test_empty_when_none_selected(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        session.add(album)
        session.commit()
        session.add(make_photo(album, immich_asset_id="a1", selected=False))
        session.commit()

        assert selected_photos(album.id, session) == []
