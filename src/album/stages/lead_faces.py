"""``lead_faces`` — resolve an album's priority subjects to Immich person IDs.

The user names the key people (the couple, the baby, the guest of honour) via
``album.yml``'s ``lead_faces`` block; this resolver turns whichever input form
they used into a set of Immich person IDs and marks the matching
[`Person`](../db/models.py) rows ``is_lead=True`` so [`select`](./select.py)
(step 18) can prioritize photos of them.

**No local face model.** Immich already recognizes every library face into a
``Person``; a ``reference_image``/``reference_asset_id`` is just a way to *name*
which people are the leads — we read Immich's own recognition, never run our own
(face embeddings aren't exposed over the API, exactly like CLIP embeddings; see
knowledge/reference/immich-api.md and knowledge/build/enrichment.md). The three inputs:

- ``person_ids`` — the Immich people, named directly. No Immich call.
- ``reference_asset_id`` — an asset already in Immich; read its faces.
- ``reference_image`` — a local photo; upload it (Immich runs face detection on
  import, *asynchronously*), wait out that detection, then read its faces.

No Prefect here — :class:`LeadFaceResolver` is a plain class (folder-structure.md
rule); ``flow.py`` wraps it as a ``@task``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Person
from album.definition import LeadFacesYAML
from album.immich.client import ImmichFace

_log = logging.getLogger(__name__)

# Immich detects faces asynchronously after an import, so a freshly-uploaded
# reference photo has no faces for a short while; poll until they appear.
_DEFAULT_POLL_INTERVAL_S = 2.0
_DEFAULT_POLL_TIMEOUT_S = 120.0


class LeadFaceResolutionError(RuntimeError):
    """A lead reference couldn't be resolved (missing file, or detection never ran)."""


class LeadFaceProvider(Protocol):
    """The slice of :class:`~album.immich.client.ImmichClient` this stage needs."""

    def upload_asset(self, path: Path) -> str: ...

    def get_faces(self, asset_id: str) -> list[ImmichFace]: ...


@dataclass
class LeadResult:
    lead_person_ids: list[str] = field(default_factory=list)
    uploaded_reference: bool = False
    # Reference faces Immich detected but hasn't assigned to a named Person —
    # surfaced so a missed subject is visible, not silent.
    unassigned_reference_faces: int = 0


class LeadFaceResolver:
    """Resolve ``lead_faces`` to Immich person IDs and mark ``Person.is_lead``.

    The Immich HTTP layer is injected as a :class:`LeadFaceProvider` (the real
    :class:`~album.immich.client.ImmichClient` satisfies it) so tests pass a fake
    without a network; ``sleep`` is injected so the async-detection wait doesn't
    actually block tests. ``run`` takes ``album_id`` first (project rule).
    """

    def __init__(
        self,
        provider: LeadFaceProvider,
        *,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s
        self._sleep = sleep

    def run(
        self,
        album_id: int,
        config: LeadFacesYAML | None,
        reference_dir: Path,
        session: Session,
    ) -> LeadResult:
        """Resolve leads for ``album_id``. No ``lead_faces`` configured ⇒ no-op.

        ``reference_dir`` is the directory ``album.yml`` lives in, used to resolve
        a relative ``reference_image``. Idempotent: an uploaded reference is cached
        on ``Album.lead_reference_asset_id`` and reused on re-run.
        """
        if config is None:
            return LeadResult()

        result = LeadResult()
        person_ids = self._resolve_person_ids(album_id, config, reference_dir, session, result)
        result.lead_person_ids = person_ids
        self._mark_leads(album_id, person_ids, session)
        if not person_ids:
            _log.warning(
                "album %s: lead_faces resolved to no named Immich people; "
                "confirm the reference people in Immich's UI so cross-album "
                "matching works",
                album_id,
            )
        return result

    def _resolve_person_ids(
        self,
        album_id: int,
        config: LeadFacesYAML,
        reference_dir: Path,
        session: Session,
        result: LeadResult,
    ) -> list[str]:
        if config.person_ids is not None:
            # Named directly — no Immich round-trip, just de-dupe (order-preserving).
            return list(dict.fromkeys(config.person_ids))

        if config.reference_asset_id is not None:
            faces = self._provider.get_faces(config.reference_asset_id)
            return self._person_ids_from_faces(faces, result)

        # reference_image: upload (once) then wait out Immich's async detection.
        asset_id = self._reference_asset_id(album_id, config, reference_dir, session, result)
        faces = self._await_faces(asset_id)
        return self._person_ids_from_faces(faces, result)

    def _reference_asset_id(
        self,
        album_id: int,
        config: LeadFacesYAML,
        reference_dir: Path,
        session: Session,
        result: LeadResult,
    ) -> str:
        album = session.get(Album, album_id)
        if album is None:
            raise ValueError(f"no album with id {album_id}")
        if album.lead_reference_asset_id is not None:
            return album.lead_reference_asset_id  # cached from a previous run — don't re-upload

        assert config.reference_image is not None  # guaranteed by the caller's dispatch
        path = reference_dir / config.reference_image
        if not path.is_file():
            raise LeadFaceResolutionError(
                f"lead_faces.reference_image {config.reference_image!r} not found at {path}"
            )
        asset_id = self._provider.upload_asset(path)
        album.lead_reference_asset_id = asset_id
        session.flush()
        result.uploaded_reference = True
        return asset_id

    def _await_faces(self, asset_id: str) -> list[ImmichFace]:
        """Poll ``get_faces`` until Immich's async detection produces faces.

        Uses the injected ``sleep`` so tests don't actually wait. Raises if the
        timeout elapses with no faces (detection stuck or the reference has none).
        """
        faces = self._provider.get_faces(asset_id)
        waited = 0.0
        while not faces and waited < self._poll_timeout_s:
            self._sleep(self._poll_interval_s)
            waited += self._poll_interval_s
            faces = self._provider.get_faces(asset_id)
        if not faces:
            raise LeadFaceResolutionError(
                f"no faces detected on reference asset {asset_id!r} after "
                f"{self._poll_timeout_s:g}s — Immich's face-detection job may not have run"
            )
        return faces

    def _person_ids_from_faces(self, faces: list[ImmichFace], result: LeadResult) -> list[str]:
        """Assigned person IDs from a reference's faces; warn on unassigned ones."""
        person_ids: list[str] = []
        for face in faces:
            if face.person_id is None:
                result.unassigned_reference_faces += 1
                _log.warning(
                    "reference face %s is unassigned in Immich; confirm the person "
                    "in Immich's UI or this lead will be missed",
                    face.id,
                )
                continue
            person_ids.append(face.person_id)
        return list(dict.fromkeys(person_ids))

    def _mark_leads(self, album_id: int, person_ids: list[str], session: Session) -> None:
        """Make the album's lead set *exactly* the resolved ``person_ids``.

        Sets ``is_lead=True`` on the resolved leads (creating any not yet synced)
        and clears it on any previously-marked lead the current ``lead_faces`` no
        longer names — so editing ``album.yml`` and re-running doesn't leave stale
        leads behind. Only reached when ``lead_faces`` is configured (``run``
        returns early otherwise), so an album with no leads is never touched.
        """
        target = set(person_ids)
        existing = {
            person.immich_person_id: person
            for person in session.scalars(select(Person).where(Person.album_id == album_id))
        }
        # Demote stale leads the current config no longer names.
        for person in existing.values():
            if person.is_lead and person.immich_person_id not in target:
                person.is_lead = False
        for person_id in person_ids:
            person = existing.get(person_id)
            if person is None:
                # A lead not yet seen in the album's synced faces — record it now so
                # a later sync/select still treats them as a lead.
                person = Person(album_id=album_id, immich_person_id=person_id, is_lead=True)
                session.add(person)
                existing[person_id] = person
            else:
                person.is_lead = True
        session.flush()
