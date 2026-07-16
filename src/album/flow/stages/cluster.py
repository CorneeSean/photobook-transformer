"""``cluster`` — assign each photo to a segment and a scene, scoped by ``album_id``.

This is where the album's structure is imposed, and it **degrades gracefully** —
the [genericity guard](/reference/folder-structure.md):

- **Segment assignment**: match each photo's UTC timestamp against the album's
  ordered [segments](/foundations/domain-model.md). No segments → one implicit segment over
  the whole album. A photo whose time falls outside every segment is *flagged*
  (returned in ``ClusterResult.out_of_range``), not dropped.
- **Scene detection** within each segment group:
  - **Temporal** (always available): a time gap over ``time_gap`` starts a new
    scene. The workhorse — an album with no segments *and* no GPS still clusters.
  - **Spatial** (when every photo in the group has GPS): DBSCAN over lat/lon
    tightens boundaries, so two nearby-in-time bursts at different places split.

Photos with a NULL timestamp can't be placed temporally; they're handled
explicitly (flagged in ``ClusterResult.undated``, left unassigned).

Pure logic over the DB — no Prefect, no external calls. Idempotent: it resets
``segment_id``/``scene_id`` for the album up front, so re-running is a no-op-equivalent.
:class:`ClusterStage`'s ``run`` takes ``album_id`` first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from sklearn.cluster import DBSCAN
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo, Segment
from album.flow.stages.base import Stage

_log = logging.getLogger(__name__)

_DEFAULT_TIME_GAP = timedelta(minutes=90)
_DEFAULT_SPATIAL_EPS_M = 200.0
_EARTH_RADIUS_M = 6_371_000.0


@dataclass
class ClusterResult:
    segments_assigned: int = 0
    scenes: int = 0
    # photo ids that are dated but fall outside every segment:
    out_of_range: list[int] = field(default_factory=list)
    # photo ids with a NULL timestamp (can't be placed):
    undated: list[int] = field(default_factory=list)


class ClusterStage(Stage[ClusterResult]):
    """`cluster` — assign each photo to a segment and a scene. Idempotent.

    Pure logic over the DB — no injected collaborator. It's a class (rather than a
    function) because it owns several private helpers plus the ``time_gap`` /
    ``spatial_eps_m`` tunables the scene detector shares; those are construction-time
    configuration, while ``run`` takes the request-scoped ``album_id`` + ``Session``.
    """

    def __init__(
        self,
        *,
        time_gap: timedelta = _DEFAULT_TIME_GAP,
        spatial_eps_m: float = _DEFAULT_SPATIAL_EPS_M,
    ) -> None:
        self._time_gap = time_gap
        self._spatial_eps_m = spatial_eps_m

    def _process(self, album: Album, session: Session, *, force: bool) -> ClusterResult:
        """Assign photos in ``album`` to segments and scenes. Idempotent."""
        photos = self._load_photos(album.id, session)
        self._reset_photos(photos)

        result = ClusterResult()
        dated = [p for p in photos if p.timestamp is not None]
        result.undated = [p.id for p in photos if p.timestamp is None]

        groups = self._assign_segments(album.segments, dated, result)
        result.scenes = self._assign_scenes(groups)

        session.flush()
        return result

    @staticmethod
    def _load_photos(album_id: int, session: Session) -> list[Photo]:
        # order_by(Photo.id): without it Postgres may return rows in any order, which
        # would make the out_of_range/undated result lists non-deterministic across runs.
        return list(
            session.scalars(select(Photo).where(Photo.album_id == album_id).order_by(Photo.id))
        )

    @staticmethod
    def _reset_photos(photos: list[Photo]) -> None:
        for photo in photos:  # reset so a re-run is deterministic
            photo.segment_id = None
            photo.scene_id = None

    def _assign_scenes(self, groups: list[list[Photo]]) -> int:
        """Sort each group temporally, index its scenes, and stamp ``scene_id``.

        Scene ids run consecutively across groups (group 1's scenes, then group
        2's, ...), so the return value is simply the running total.
        """
        next_scene = 1
        for group_photos in groups:
            group_photos.sort(key=lambda p: _as_naive_utc(p.timestamp))
            local = self._scene_indices(group_photos)
            for photo, idx in zip(group_photos, local, strict=True):
                photo.scene_id = next_scene + idx
            next_scene += (max(local) + 1) if local else 0
        return next_scene - 1

    def _assign_segments(
        self, segments: list[Segment], dated: list[Photo], result: ClusterResult
    ) -> list[list[Photo]]:
        """Return the photo groups to run scene detection over, in a deterministic order.

        With segments: one group per segment (in ``position`` order), photos outside
        all segments flagged. Without segments: a single implicit group.
        """
        if not segments:
            return [dated] if dated else []
        by_segment = self._bucket_by_segment(segments, dated, result)
        return [by_segment[seg.id] for seg in segments if by_segment[seg.id]]

    def _bucket_by_segment(
        self, segments: list[Segment], dated: list[Photo], result: ClusterResult
    ) -> dict[int, list[Photo]]:
        by_segment: dict[int, list[Photo]] = {seg.id: [] for seg in segments}
        for photo in dated:
            segment = self._find_segment(segments, photo.timestamp)
            if segment is None:
                result.out_of_range.append(photo.id)
                continue
            photo.segment_id = segment.id
            result.segments_assigned += 1
            by_segment[segment.id].append(photo)
        return by_segment

    @staticmethod
    def _find_segment(segments: list[Segment], timestamp: datetime | None) -> Segment | None:
        # Bounds are inclusive on both ends; segments are guaranteed non-overlapping by
        # definition.py, so the only ambiguous case is two segments abutting on the same
        # instant (end_at == next start_at), where first-match (position order) wins.
        ts = _as_naive_utc(timestamp)
        for segment in segments:
            if _as_naive_utc(segment.start_at) <= ts <= _as_naive_utc(segment.end_at):
                return segment
        return None

    def _scene_indices(self, photos: list[Photo]) -> list[int]:
        """0-based, contiguous scene index per (time-sorted) photo within one group."""
        if not photos:
            return []
        spatial = self._spatial_labels(photos) if self._has_gps(photos) else None

        indices = [0]
        for i in range(1, len(photos)):
            is_break = self._is_scene_break(photos, spatial, i)
            indices.append(indices[-1] + (1 if is_break else 0))
        return indices

    @staticmethod
    def _has_gps(photos: list[Photo]) -> bool:
        return all(p.gps_lat is not None and p.gps_lon is not None for p in photos)

    def _is_scene_break(self, photos: list[Photo], spatial: list[int] | None, i: int) -> bool:
        gap_break = (
            _as_naive_utc(photos[i].timestamp) - _as_naive_utc(photos[i - 1].timestamp)
        ) > self._time_gap
        spatial_break = spatial is not None and spatial[i] != spatial[i - 1]
        return gap_break or spatial_break

    def _spatial_labels(self, photos: list[Photo]) -> list[int]:
        """DBSCAN cluster label per photo over lat/lon (haversine). min_samples=1 → no noise."""
        # The `if` filter both satisfies the type checker (float, not float | None) and
        # is a no-op: callers only invoke this when every photo has coordinates.
        coords = np.radians(
            [
                [p.gps_lat, p.gps_lon]
                for p in photos
                if p.gps_lat is not None and p.gps_lon is not None
            ]
        )
        labels = DBSCAN(
            eps=self._spatial_eps_m / _EARTH_RADIUS_M, min_samples=1, metric="haversine"
        ).fit(coords)
        return labels.labels_.tolist()


def _as_naive_utc(dt: datetime | None) -> datetime:
    """Normalize to naive-UTC for comparison. Everything is UTC by invariant, but
    SQLite returns naive datetimes while Postgres/in-memory objects are aware —
    normalize both sides so we never compare naive against aware."""
    if dt is None:  # callers pass only non-None timestamps here
        raise ValueError("cannot order a photo with no timestamp")
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt
