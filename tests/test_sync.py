"""Tests for the ``sync`` stage: Immich assets -> DB, over in-memory SQLite.

The Immich HTTP layer is stubbed by a fake ``AssetSource`` returning canned
``ImmichAsset`` objects, so these tests stay pure-logic and fast (working-agreement.md).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.immich.client import ImmichAsset
from album.stages.sync import sync

from .conftest import make_album


class FakeImmich:
    """An in-memory :class:`~album.stages.sync.AssetSource` for tests."""

    def __init__(self, assets: list[ImmichAsset]) -> None:
        self._assets = assets
        self.calls: list[str] = []

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]:
        self.calls.append(immich_album_id)
        return self._assets


def _asset(asset_id: str, **exif: object) -> ImmichAsset:
    """Build an asset the way it arrives on the wire, keyed by Immich's field aliases."""
    return ImmichAsset.model_validate(
        {
            "id": asset_id,
            "originalFileName": f"{asset_id}.jpg",
            "exifInfo": exif or None,
        }
    )


def _utc(dt: datetime | None) -> datetime | None:
    """Re-attach UTC for comparison: SQLite drops tzinfo that Postgres would keep."""
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _persist(session: Session, album: Album) -> int:
    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


def test_creates_photos_scoped_to_album(session: Session) -> None:
    album_id = _persist(session, make_album(immich_album_id="immich-abc"))
    client = FakeImmich([_asset("a1"), _asset("a2")])

    result = sync(album_id, session, client)

    assert client.calls == ["immich-abc"]
    assert result.created == 2
    photos = session.scalars(select(Photo)).all()
    assert {p.immich_asset_id for p in photos} == {"a1", "a2"}
    assert all(p.album_id == album_id for p in photos)


def test_does_not_touch_other_albums(session: Session) -> None:
    kept = _persist(session, make_album(slug="other", immich_album_id="immich-other"))
    session.add(Photo(album_id=kept, immich_asset_id="keep-me", filename="keep.jpg"))
    target = _persist(session, make_album(slug="target", immich_album_id="immich-target"))
    session.commit()

    sync(target, session, FakeImmich([_asset("a1")]))

    other_photos = session.scalars(select(Photo).where(Photo.album_id == kept)).all()
    assert [p.immich_asset_id for p in other_photos] == ["keep-me"]


def test_is_idempotent(session: Session) -> None:
    album_id = _persist(session, make_album(immich_album_id="immich-abc"))
    client = FakeImmich([_asset("a1", model="X-T5")])

    first = sync(album_id, session, client)
    second = sync(album_id, session, client)

    assert (first.created, first.updated) == (1, 0)
    assert (second.created, second.updated) == (0, 1)
    assert len(session.scalars(select(Photo)).all()) == 1


def test_offset_bearing_time_normalized_to_utc(session: Session) -> None:
    album_id = _persist(session, make_album(immich_album_id="immich-abc"))
    # 12:30 in Podgorica (UTC+2) already anchored via 'Z' in Immich's payload.
    aware = datetime(2026, 5, 2, 12, 30, tzinfo=UTC)
    client = FakeImmich([_asset("a1", dateTimeOriginal=aware)])

    sync(album_id, session, client)

    photo = session.scalars(select(Photo)).one()
    assert _utc(photo.timestamp) == datetime(2026, 5, 2, 12, 30, tzinfo=UTC)


def test_naive_time_uses_exif_timezone(session: Session) -> None:
    album_id = _persist(session, make_album(immich_album_id="immich-abc", timezone=None))
    naive = datetime(2026, 5, 2, 14, 30)  # noqa: DTZ001 -- deliberately naive
    client = FakeImmich([_asset("a1", dateTimeOriginal=naive, timeZone="Europe/Podgorica")])

    sync(album_id, session, client)

    photo = session.scalars(select(Photo)).one()
    # 14:30 in UTC+2 -> 12:30 UTC.
    assert _utc(photo.timestamp) == datetime(2026, 5, 2, 12, 30, tzinfo=UTC)


def test_naive_time_falls_back_to_album_timezone(session: Session) -> None:
    album_id = _persist(session, make_album(immich_album_id="immich-abc", timezone="Europe/London"))
    naive = datetime(2026, 5, 2, 14, 30)  # noqa: DTZ001 -- deliberately naive
    client = FakeImmich([_asset("a1", dateTimeOriginal=naive)])  # no exif timeZone

    sync(album_id, session, client)

    photo = session.scalars(select(Photo)).one()
    # 14:30 BST (UTC+1) -> 13:30 UTC.
    assert _utc(photo.timestamp) == datetime(2026, 5, 2, 13, 30, tzinfo=UTC)


def test_ambiguous_timezone_is_flagged(session: Session, caplog: pytest.LogCaptureFixture) -> None:
    album_id = _persist(session, make_album(immich_album_id="immich-abc", timezone=None))
    naive = datetime(2026, 5, 2, 14, 30)  # noqa: DTZ001 -- deliberately naive
    client = FakeImmich([_asset("a1", dateTimeOriginal=naive)])  # no zone anywhere

    with caplog.at_level(logging.WARNING):
        result = sync(album_id, session, client)

    assert result.ambiguous == ["a1"]
    assert "ambiguous" in caplog.text
    photo = session.scalars(select(Photo)).one()
    assert photo.timestamp is None


def test_maps_gps_camera_and_duplicate_group(session: Session) -> None:
    album_id = _persist(session, make_album(immich_album_id="immich-abc"))
    asset = ImmichAsset.model_validate(
        {
            "id": "a1",
            "originalFileName": "IMG.jpg",
            "duplicateId": "dup-7",
            "exifInfo": {
                "make": "Apple",
                "model": "iPhone 15 Pro",
                "latitude": 42.4,
                "longitude": 18.7,
            },
        }
    )
    sync(album_id, session, FakeImmich([asset]))

    photo = session.scalars(select(Photo)).one()
    assert photo.duplicate_group_id == "dup-7"
    assert photo.camera_model == "Apple iPhone 15 Pro"
    assert (photo.gps_lat, photo.gps_lon) == (42.4, 18.7)
