"""``sync`` — pull an album's assets from Immich into our DB, scoped by ``album_id``.

This is *the boundary*: everything downstream reads from our DB, never from Immich
directly. Idempotent — re-running updates existing ``Photo`` rows in place rather
than duplicating them (keyed on Immich's asset id).

Each photo's capture time is normalized to **UTC** (data-model.md invariant). The
source zone is resolved in priority order:

1. an explicit offset on ``exifInfo.dateTimeOriginal`` (already an absolute instant),
2. else the IANA ``exifInfo.timeZone`` Immich reports, applied to the local time,
3. else the album's default ``timezone``,
4. else the photo is *flagged* (returned in ``SyncResult.ambiguous``) and its
   timestamp left ``NULL`` — cluster (step 7) handles NULL-timestamp photos
   explicitly rather than dropping them.

No Prefect imports live here — :class:`SyncStage` is a plain class so it stays
unit-testable without an orchestrator (folder-structure.md rule).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Person, Photo
from album.immich.client import ImmichAsset, ImmichExif, ImmichFace

_log = logging.getLogger(__name__)


class AssetSource(Protocol):
    """The slice of :class:`~album.immich.client.ImmichClient` that ``sync`` needs.

    Declared as a Protocol so tests can pass a fake returning canned assets without
    an HTTP layer, and the real client satisfies it structurally.
    """

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]: ...

    def get_faces(self, asset_id: str) -> list[ImmichFace]: ...


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    # Immich asset ids whose capture zone couldn't be determined (timestamp left NULL).
    ambiguous: list[str] = field(default_factory=list)
    # Immich asset ids whose face fetch failed (face columns left untouched/NULL).
    face_errors: list[str] = field(default_factory=list)


class SyncStage:
    """`sync` — pull an Immich album's assets into our DB. Idempotent.

    The Immich HTTP layer is the one stable collaborator, injected as an
    :class:`AssetSource` (:class:`~album.immich.client.ImmichClient` satisfies it)
    so tests pass a fake without a network. ``run`` takes ``album_id`` first, then
    the request-scoped ``Session`` (never a construction-time dependency).
    """

    def __init__(self, source: AssetSource) -> None:
        self._source = source

    def run(self, album_id: int, session: Session) -> SyncResult:
        """Sync the Immich album linked to ``album_id`` into our DB. Idempotent."""
        album = session.get(Album, album_id)
        if album is None:
            raise ValueError(f"no album with id {album_id}")
        if album.immich_album_id is None:
            raise ValueError(f"album {album.slug!r} has no immich_album_id set")

        default_zone = self._resolve_zone(album.timezone)
        existing = {
            photo.immich_asset_id: photo
            for photo in session.scalars(select(Photo).where(Photo.album_id == album_id))
        }
        # Album-scoped Person cache, keyed by Immich id so re-sync upserts in place.
        people = {
            person.immich_person_id: person
            for person in session.scalars(select(Person).where(Person.album_id == album_id))
        }

        result = SyncResult()
        for asset in self._source.get_album_assets(album.immich_album_id):
            timestamp, ambiguous = self._capture_time_utc(asset, default_zone)
            if ambiguous:
                result.ambiguous.append(asset.id)
                _log.warning(
                    "asset %s (%s): capture-time zone is ambiguous; leaving timestamp NULL",
                    asset.id,
                    asset.original_file_name,
                )

            photo = existing.get(asset.id)
            if photo is None:
                photo = Photo(album_id=album_id, immich_asset_id=asset.id)
                session.add(photo)
                result.created += 1
            else:
                result.updated += 1

            photo.filename = asset.original_file_name
            photo.timestamp = timestamp
            photo.duplicate_group_id = asset.duplicate_id
            exif = asset.exif_info
            photo.camera_model = self._camera_model(exif)
            photo.gps_lat = exif.latitude if exif else None
            photo.gps_lon = exif.longitude if exif else None

            self._sync_faces(album_id, asset, photo, people, session, result)

        session.flush()
        return result

    def _sync_faces(
        self,
        album_id: int,
        asset: ImmichAsset,
        photo: Photo,
        people: dict[str, Person],
        session: Session,
        result: SyncResult,
    ) -> None:
        """Populate ``photo.face_count``/``person_ids`` and upsert ``Person`` rows.

        Failure-isolated: a face fetch *or parse* failing for one asset is logged
        and recorded, leaving that photo's face columns untouched (NULL on a first
        sync) rather than aborting the whole album — the same isolation the
        ambiguous-timestamp path uses. ``people`` is mutated in place so a person
        seen on an earlier asset isn't inserted twice within one run.
        """
        # NB: two separate `except` clauses, not `except (A, B):` — ruff 0.15.21's
        # py314 formatter mangles the parenthesized-tuple form (see log.md
        # 2026-07-13). A transport failure (httpx.HTTPError) OR a malformed face
        # payload (pydantic ValidationError, raised inside get_faces) must both be
        # isolated to this one asset, never abort the album mid-loop.
        try:
            faces = self._source.get_faces(asset.id)
        except httpx.HTTPError:
            self._record_face_error(asset, result)
            return
        except ValidationError:
            self._record_face_error(asset, result)
            return

        photo.face_count = len(faces)
        # Assigned faces only, de-duplicated but order-preserving (a person may
        # appear in more than one face on the same photo).
        photo.person_ids = list(
            dict.fromkeys(face.person_id for face in faces if face.person_id is not None)
        )

        for face in faces:
            if face.person_id is None:
                continue
            person = people.get(face.person_id)
            if person is None:
                person = Person(album_id=album_id, immich_person_id=face.person_id)
                session.add(person)
                people[face.person_id] = person
            # Let a rename in Immich propagate, but never clobber a known name with None.
            if face.person_name is not None:
                person.name = face.person_name

    def _record_face_error(self, asset: ImmichAsset, result: SyncResult) -> None:
        """Record a per-asset face fetch/parse failure without aborting the album."""
        result.face_errors.append(asset.id)
        _log.warning(
            "asset %s (%s): faces fetch/parse failed; leaving face columns unchanged",
            asset.id,
            asset.original_file_name,
            exc_info=True,
        )

    def _capture_time_utc(
        self, asset: ImmichAsset, default_zone: ZoneInfo | None
    ) -> tuple[datetime | None, bool]:
        """Return ``(utc_timestamp, ambiguous)`` for an asset.

        ``ambiguous`` is True only when a naive local time can't be anchored to any
        zone; a missing time entirely is not ambiguous, just absent.
        """
        exif = asset.exif_info
        candidate = (exif.date_time_original if exif else None) or asset.local_date_time
        if candidate is None:
            return None, False
        if candidate.tzinfo is not None:
            return candidate.astimezone(UTC), False

        zone = self._resolve_zone(exif.time_zone if exif else None) or default_zone
        if zone is None:
            return None, True
        return candidate.replace(tzinfo=zone).astimezone(UTC), False

    def _resolve_zone(self, name: str | None) -> ZoneInfo | None:
        if not name:
            return None
        # NB: two separate `except` clauses rather than `except (A, B):` — ruff 0.15.21's
        # formatter mangles the parenthesized-tuple form into invalid Python under the
        # py314 target (see log.md 2026-07-13). Immich occasionally reports non-IANA
        # zone strings (e.g. "UTC+02:00"); treat any unresolvable name as unknown so the
        # caller falls back rather than crashing a run.
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
        except ValueError:
            pass
        _log.warning("unrecognized timezone %r from Immich; ignoring", name)
        return None

    def _camera_model(self, exif: ImmichExif | None) -> str | None:
        if exif is None or exif.model is None:
            return None
        if exif.make and not exif.model.startswith(exif.make):
            return f"{exif.make} {exif.model}"
        return exif.model
