"""Tests for :class:`~album.flow.stages.sync.SyncStage`: Immich assets -> DB, over
in-memory SQLite.

The Immich HTTP layer is stubbed by a fake ``AssetSource`` returning canned
``ImmichAsset`` objects, so these tests stay pure-logic and fast (working-agreement.md).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Person, Photo
from album.db.session import reuse
from album.flow.stages.sync import SyncStage
from album.immich.client import ImmichAsset, ImmichFace


def _face(face_id: str, person_id: str | None = None, name: str | None = None) -> ImmichFace:
    """Build a face the way it arrives on the wire (person nested, null if unassigned)."""
    person = {"id": person_id, "name": name} if person_id is not None else None
    payload = {"id": face_id, "sourceType": "machine-learning", "person": person}
    return ImmichFace.model_validate(payload)


class FakeImmich:
    """An in-memory :class:`~album.flow.stages.sync.AssetSource` for tests.

    Faces are canned per asset id; an asset id listed in ``face_errors`` raises
    from ``get_faces`` to exercise `sync`'s per-asset failure isolation.
    """

    def __init__(
        self,
        assets: list[ImmichAsset],
        *,
        faces: dict[str, list[ImmichFace]] | None = None,
        face_errors: set[str] | None = None,
        face_parse_errors: set[str] | None = None,
    ) -> None:
        self._assets = assets
        self._faces = faces or {}
        self._face_errors = face_errors or set()
        self._face_parse_errors = face_parse_errors or set()
        self.calls: list[str] = []
        self.face_calls: list[str] = []

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]:
        self.calls.append(immich_album_id)
        return self._assets

    def get_faces(self, asset_id: str) -> list[ImmichFace]:
        self.face_calls.append(asset_id)
        if asset_id in self._face_errors:
            raise httpx.HTTPError(f"boom for {asset_id}")
        if asset_id in self._face_parse_errors:
            # A malformed payload — exactly what the real client raises mid-parse.
            ImmichFace.model_validate({"sourceType": "machine-learning"})  # missing required id
        return self._faces.get(asset_id, [])


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

        result = SyncStage(client, album_id, reuse(session)).run()

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

        SyncStage(FakeImmich([self._asset("a1")]), target, reuse(session)).run()

        other_photos = session.scalars(select(Photo).where(Photo.album_id == kept)).all()
        assert [p.immich_asset_id for p in other_photos] == ["keep-me"]

    def test_is_idempotent(self, session: Session, make_album: Callable[..., Album]) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
        source = FakeImmich([self._asset("a1", model="X-T5")])

        first = SyncStage(source, album_id, reuse(session)).run()
        second = SyncStage(source, album_id, reuse(session)).run()

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

        SyncStage(FakeImmich([self._asset("a1", **asset_kwargs)]), album_id, reuse(session)).run()

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
            result = SyncStage(client, album_id, reuse(session)).run()

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
        SyncStage(FakeImmich([asset]), album_id, reuse(session)).run()

        photo = session.scalars(select(Photo)).one()
        assert photo.duplicate_group_id == "dup-7"
        assert photo.camera_model == "Apple iPhone 15 Pro"
        assert (photo.gps_lat, photo.gps_lon) == (42.4, 18.7)

    def test_populates_faces_and_upserts_people(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
        client = FakeImmich(
            [self._asset("a1")],
            faces={
                "a1": [
                    _face("f1", "p-alex", "Alex"),
                    _face("f2", "p-sam", "Sam"),
                    _face("f3"),  # detected but unassigned (person: null)
                ]
            },
        )

        SyncStage(client, album_id, reuse(session)).run()

        assert client.face_calls == ["a1"]
        photo = session.scalars(select(Photo)).one()
        # face_count counts *all* faces; person_ids holds only assigned ones.
        assert photo.face_count == 3
        assert photo.person_ids == ["p-alex", "p-sam"]
        people = {p.immich_person_id: p for p in session.scalars(select(Person))}
        assert set(people) == {"p-alex", "p-sam"}
        assert people["p-alex"].name == "Alex"
        assert all(p.album_id == album_id and not p.is_lead for p in people.values())

    def test_faceless_asset_lands_empty_not_null(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))

        SyncStage(FakeImmich([self._asset("a1")], faces={"a1": []}), album_id, reuse(session)).run()

        photo = session.scalars(select(Photo)).one()
        # Fetched but faceless — distinct from "not fetched yet" (NULL).
        assert photo.face_count == 0
        assert photo.person_ids == []

    def test_sync_faces_idempotent(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
        SyncStage(
            FakeImmich([self._asset("a1")], faces={"a1": [_face("f1", "p-alex", "Alex")]}),
            album_id,
            reuse(session),
        ).run()
        # A rename in Immich propagates on re-sync, without duplicating the row.
        SyncStage(
            FakeImmich([self._asset("a1")], faces={"a1": [_face("f1", "p-alex", "Alex M.")]}),
            album_id,
            reuse(session),
        ).run()

        people = session.scalars(select(Person)).all()
        assert len(people) == 1
        assert people[0].name == "Alex M."

    def test_dedups_person_ids_across_faces(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
        # Two detected faces of the same person on one photo (plus a second person).
        client = FakeImmich(
            [self._asset("a1")],
            faces={
                "a1": [
                    _face("f1", "p-alex", "Alex"),
                    _face("f2", "p-sam", "Sam"),
                    _face("f3", "p-alex", "Alex"),
                ]
            },
        )

        SyncStage(client, album_id, reuse(session)).run()

        photo = session.scalars(select(Photo)).one()
        assert photo.face_count == 3  # all faces counted
        # person_ids de-duplicated, first-seen order preserved.
        assert photo.person_ids == ["p-alex", "p-sam"]
        assert len(session.scalars(select(Person)).all()) == 2

    @pytest.mark.parametrize(
        ("kind", "kwargs"),
        [
            ("transport", {"face_errors": {"a1"}}),  # httpx.HTTPError from get_faces
            ("parse", {"face_parse_errors": {"a1"}}),  # pydantic.ValidationError mid-parse
        ],
    )
    def test_face_fetch_failure_is_isolated(
        self,
        session: Session,
        make_album: Callable[..., Album],
        caplog: pytest.LogCaptureFixture,
        kind: str,
        kwargs: dict[str, set[str]],
    ) -> None:
        album_id = self._persist(session, make_album(immich_album_id="immich-abc"))
        client = FakeImmich(
            [self._asset("a1"), self._asset("a2")],
            faces={"a2": [_face("f1", "p-sam", "Sam")]},
            **kwargs,  # a1's face call blows up (transport or parse); a2 is fine
        )

        with caplog.at_level(logging.WARNING):
            result = SyncStage(client, album_id, reuse(session)).run()

        assert result.face_errors == ["a1"]
        assert "faces" in caplog.text
        photos = {p.immich_asset_id: p for p in session.scalars(select(Photo))}
        # a1's face columns stay NULL (never aborts the album); a2 is unaffected.
        assert (photos["a1"].face_count, photos["a1"].person_ids) == (None, None)
        assert photos["a2"].face_count == 1
        assert photos["a2"].person_ids == ["p-sam"]
