"""Tests for the ``cluster`` stage over in-memory SQLite — the genericity guard.

Thorough by design (build-order step 7): segment assignment, the no-segments and
no-GPS temporal fallbacks, the GPS spatial path, and the out-of-range / undated
edge cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.stages.cluster import ClusterResult, cluster

from .conftest import make_album, make_photo, make_segment

# Two far-apart coordinates (Lisbon vs Porto, ~250 km) — distinct spatial clusters.
_KOTOR = (42.424, 18.771)
_ZAGREB = (45.815, 15.982)


def _dt(hour: int, minute: int = 0, day: int = 2) -> datetime:
    return datetime(2026, 5, day, hour, minute, tzinfo=UTC)


def _persist(session: Session, album: Album) -> int:
    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


def _photo(album: Album, asset: str, ts: datetime | None, gps: tuple[float, float] | None = None):
    return make_photo(
        album,
        immich_asset_id=asset,
        filename=f"{asset}.jpg",
        timestamp=ts,
        gps_lat=gps[0] if gps else None,
        gps_lon=gps[1] if gps else None,
    )


def _by_asset(session: Session) -> dict[str, Photo]:
    return {p.immich_asset_id: p for p in session.scalars(select(Photo))}


def test_assigns_photos_to_segments_by_timestamp(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    day1 = make_segment(
        album, position=1, label="Day 1", start_at=_dt(0, day=2), end_at=_dt(23, 59, day=2)
    )
    day2 = make_segment(
        album, position=2, label="Day 2", start_at=_dt(0, day=3), end_at=_dt(23, 59, day=3)
    )
    album.segments = [day1, day2]
    album.photos = [
        _photo(album, "p1", _dt(12, day=2)),
        _photo(album, "p2", _dt(12, day=3)),
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert photos["p1"].segment_id == day1.id
    assert photos["p2"].segment_id == day2.id
    assert result.segments_assigned == 2
    assert result.out_of_range == []


def test_no_segments_still_produces_scenes(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")  # no segments
    album.photos = [
        _photo(album, "p1", _dt(10, 0)),
        _photo(album, "p2", _dt(10, 30)),  # same scene (30 min gap)
        _photo(album, "p3", _dt(14, 0)),  # new scene (>90 min gap)
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert all(p.segment_id is None for p in photos.values())  # implicit single segment
    assert photos["p1"].scene_id == photos["p2"].scene_id
    assert photos["p1"].scene_id != photos["p3"].scene_id
    assert result.scenes == 2


def test_no_gps_uses_temporal_fallback(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        _photo(album, "p1", _dt(9, 0)),
        _photo(album, "p2", _dt(13, 0)),  # >90 min gap
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert photos["p1"].scene_id != photos["p2"].scene_id
    assert result.scenes == 2


def test_with_gps_takes_spatial_path(session: Session) -> None:
    # All four within 30 minutes → no temporal split — only the spatial path can
    # separate the two locations into two scenes.
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        _photo(album, "p1", _dt(10, 0), _KOTOR),
        _photo(album, "p2", _dt(10, 10), _KOTOR),
        _photo(album, "p3", _dt(10, 20), _ZAGREB),
        _photo(album, "p4", _dt(10, 30), _ZAGREB),
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert photos["p1"].scene_id == photos["p2"].scene_id
    assert photos["p3"].scene_id == photos["p4"].scene_id
    assert photos["p1"].scene_id != photos["p3"].scene_id
    assert result.scenes == 2


def test_gps_revisit_after_time_gap_still_splits(session: Session) -> None:
    # Same place twice, but hours apart: the spatial label is identical, so only
    # the temporal gap can (and must) split them — spatial + temporal cooperate.
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        _photo(album, "p1", _dt(10, 0), _KOTOR),
        _photo(album, "p2", _dt(10, 10), _KOTOR),
        _photo(album, "p3", _dt(14, 0), _KOTOR),  # same place, >90 min later
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert photos["p1"].scene_id == photos["p2"].scene_id
    assert photos["p3"].scene_id != photos["p1"].scene_id
    assert result.scenes == 2


def test_mixed_gps_falls_back_to_temporal(session: Session) -> None:
    # Not every photo has GPS, so the spatial path is skipped for the group; the
    # two (close in time, different places) stay one scene on the temporal path.
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        _photo(album, "p1", _dt(10, 0), _KOTOR),
        _photo(album, "p2", _dt(10, 10), None),  # no GPS → disables spatial for the group
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert photos["p1"].scene_id == photos["p2"].scene_id
    assert result.scenes == 1


def test_photo_outside_all_segments_is_flagged_not_dropped(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    day1 = make_segment(
        album, position=1, label="Day 1", start_at=_dt(0, day=2), end_at=_dt(23, 59, day=2)
    )
    album.segments = [day1]
    album.photos = [
        _photo(album, "inside", _dt(12, day=2)),
        _photo(album, "outside", _dt(12, day=5)),  # no segment covers May 5
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert photos["inside"].segment_id == day1.id
    assert photos["outside"].segment_id is None
    assert photos["outside"].scene_id is None
    assert result.out_of_range == [photos["outside"].id]
    # Not dropped — still present in the DB.
    assert session.scalars(select(Photo)).all().__len__() == 2


def test_null_timestamp_handled_explicitly(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        _photo(album, "dated", _dt(10, 0)),
        _photo(album, "undated", None),
    ]
    album_id = _persist(session, album)

    result = cluster(album_id, session)

    photos = _by_asset(session)
    assert photos["undated"].segment_id is None
    assert photos["undated"].scene_id is None
    assert result.undated == [photos["undated"].id]
    assert photos["dated"].scene_id is not None  # the dated one still clustered


def test_is_idempotent(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        _photo(album, "p1", _dt(10, 0)),
        _photo(album, "p2", _dt(14, 0)),
    ]
    album_id = _persist(session, album)

    first = cluster(album_id, session)
    before = {a: p.scene_id for a, p in _by_asset(session).items()}
    second = cluster(album_id, session)
    after = {a: p.scene_id for a, p in _by_asset(session).items()}

    assert first == second == ClusterResult(scenes=2)
    assert before == after


def test_scoped_to_album(session: Session) -> None:
    other = make_album(slug="other", immich_album_id="immich-other")
    other.photos = [_photo(other, "o1", _dt(10, 0))]
    other_id = _persist(session, other)
    target = make_album(slug="target", immich_album_id="immich-target")
    target.photos = [_photo(target, "t1", _dt(10, 0))]
    target_id = _persist(session, target)

    cluster(target_id, session)

    other_photo = session.scalars(select(Photo).where(Photo.album_id == other_id)).one()
    assert other_photo.scene_id is None  # untouched
