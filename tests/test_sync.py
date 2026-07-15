"""Tests for :class:`~album.stages.sync.SyncStage`: Immich assets -> DB, over
in-memory SQLite.

The Immich HTTP layer is stubbed by a fake ``AssetSource`` returning canned
``ImmichAsset`` objects, so these tests stay pure-logic and fast (working-agreement.md).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.immich.client import ImmichAsset
from album.stages.sync import SyncStage


class FakeImmich:
    """An in-memory :class:`~album.stages.sync.AssetSource` for tests."""

    def __init__(self, assets: list[ImmichAsset]) -> None:
        self._assets = assets
        self.calls: list[str] = []

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]:
        self.calls.append(immich_album_id)
        return self._assets


class TestSync:
    """`sync`: Immich assets → DB, scoping, idempotency, and UTC normalization."""

    def _asset(self, asset_id: str, **exif: object) -> ImmichAsset:
        """Build an asset the way it arrives on the wire, keyed by Immich's aliases."""
        return ImmichAsset.model_validate(
            {
                "id": asset_id,
                "originalFileName": f"{asset_id}.jpg",
                "exifInfo": exif or None,
            }
        )

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def _utc(self, dt: datetime | None) -> datetime | None:
        """Re-attach UTC for comparison: SQLite drops tzinfo that Postgres keeps."""
        return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)

    def test_creates_photos_scoped_to_album(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
        client = FakeImmich([self._asset("a1"), self._asset("a2")])

        result = SyncStage(client).run(album_id, session)

        assert client.calls == ["immich-abc"]
        assert result.created == 2
        photos = session.scalars(select(Photo)).all()
        assert {p.immich_asset_id for p in photos} == {"a1", "a2"}
        assert all(p.album_id == album_id for p in photos)

    def test_does_not_touch_other_albums(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        kept = self._persist(session, make_album(slug="other", immich_album_id="immich-other"))
        session.add(Photo(album_id=kept, immich_asset_id="keep-me", filename="keep.jpg"))
        target = self._persist(session, make_album(slug="target", immich_album_id="immich-target"))
        session.commit()

        SyncStage(FakeImmich([self._asset("a1")])).run(target, session)

        other_photos = session.scalars(select(Photo).where(Photo.album_id == kept)).all()
        assert [p.immich_asset_id for p in other_photos] == ["keep-me"]

    def test_is_idempotent(self, session: Session, make_album: Callable[..., Album]) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
        stage = SyncStage(FakeImmich([self._asset("a1", model="X-T5")]))

        first = stage.run(album_id, session)
        second = stage.run(album_id, session)

        assert (first.created, first.updated) == (1, 0)
        assert (second.created, second.updated) == (0, 1)
        assert len(session.scalars(select(Photo)).all()) == 1

    @pytest.mark.parametrize(
        ("album_kwargs", "asset_kwargs", "expected"),
        [
            # aware time already anchored via 'Z' — normalized regardless of album tz.
            (
                {},
                {"dateTimeOriginal": datetime(2026, 5, 2, 12, 30, tzinfo=UTC)},
                (2026, 5, 2, 12, 30),
            ),
            # naive + exif timeZone Europe/Podgorica (UTC+2): 14:30 → 12:30 UTC.
            (
                {"timezone": None},
                {"dateTimeOriginal": datetime(2026, 5, 2, 14, 30), "timeZone": "Europe/Podgorica"},  # noqa: DTZ001
                (2026, 5, 2, 12, 30),
            ),
            # naive + album fallback Europe/London (BST, UTC+1): 14:30 → 13:30 UTC.
            (
                {"timezone": "Europe/London"},
                {"dateTimeOriginal": datetime(2026, 5, 2, 14, 30)},  # noqa: DTZ001
                (2026, 5, 2, 13, 30),
            ),
        ],
        ids=["aware-offset", "exif-zone", "album-fallback"],
    )
    def test_capture_time_normalized_to_utc(
        self,
        session: Session,
        make_album: Callable[..., Album],
        album_kwargs: dict[str, object],
        asset_kwargs: dict[str, object],
        expected: tuple[int, ...],
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc", **album_kwargs))

        SyncStage(FakeImmich([self._asset("a1", **asset_kwargs)])).run(album_id, session)

        photo = session.scalars(select(Photo)).one()
        assert self._utc(photo.timestamp) == datetime(*expected, tzinfo=UTC)

    def test_ambiguous_timezone_is_flagged(
        self,
        session: Session,
        make_album: Callable[..., Album],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc", timezone=None))
        naive = datetime(2026, 5, 2, 14, 30)  # noqa: DTZ001 -- deliberately naive
        client = FakeImmich([self._asset("a1", dateTimeOriginal=naive)])  # no zone anywhere

        with caplog.at_level(logging.WARNING):
            result = SyncStage(client).run(album_id, session)

        assert result.ambiguous == ["a1"]
        assert "ambiguous" in caplog.text
        photo = session.scalars(select(Photo)).one()
        assert photo.timestamp is None

    def test_maps_gps_camera_and_duplicate_group(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
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
        SyncStage(FakeImmich([asset])).run(album_id, session)

        photo = session.scalars(select(Photo)).one()
        assert photo.duplicate_group_id == "dup-7"
        assert photo.camera_model == "Apple iPhone 15 Pro"
        assert (photo.gps_lat, photo.gps_lon) == (42.4, 18.7)
