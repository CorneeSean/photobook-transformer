"""``lead_faces`` — resolve an album's priority subjects to Immich person IDs.

The user names the key people (the couple, the baby, the guest of honour) via
``album.yml``'s ``lead_faces`` block; this resolver turns whichever input form
they used into a set of Immich person IDs and marks the matching
[`Person`](../db/models.py) rows ``is_lead=True`` so [`select`](./select.py)
(step 18) can prioritize photos of them.

**No local face model.** Immich already recognizes every library face into a
``Person``; ``reference_images``/``reference_asset_ids`` are just ways to *name*
which people are the leads — we read Immich's own recognition, never run our own
(face embeddings aren't exposed over the API, exactly like CLIP embeddings; see
knowledge/reference/immich-api.md and knowledge/build/enrichment.md). Every input
is a list (naming one lead is the one-element case). The three inputs:

- ``person_ids`` — the Immich people, named directly. No Immich call.
- ``reference_asset_ids`` — assets already in Immich; read their faces.
- ``reference_images`` — local photos; upload each (Immich runs face detection on
  import, *asynchronously*), wait out that detection, then union their faces.

:class:`LeadFaceResolver` stays Prefect-free logic (folder-structure.md rule) — so
it's unit-testable without an orchestrator via ``run``. It is **not** a
:class:`~album.flow.stages.base.Stage` (it has no ``Album`` envelope) but *is* a
sibling :class:`~album.flow.stages.base.TaskUnit`: it takes its extra per-run inputs
(the parsed ``lead_faces`` config and its directory, which the
:class:`~album.flow._flow.Pipeline` reads from ``album.yml``) plus a
``session_factory`` at construction, exposes a no-argument ``run`` that opens the
session and commits explicitly, and runs via ``to_task().submit()`` like the fan-out
stages.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Person
from album.db.session import SessionFactory
from album.definition import LeadFacesYAML
from album.flow.stages.base import TaskUnit
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


class LeadFaceResolver(TaskUnit[LeadResult]):
    """Resolve ``lead_faces`` to Immich person IDs and mark ``Person.is_lead``.

    Not a :class:`~album.flow.stages.base.Stage` (it's a resolver invoked by
    ``select``, not a DAG stage with an ``Album`` short-circuit) but a sibling
    :class:`~album.flow.stages.base.TaskUnit`: it too takes its ``session_factory``
    (plus its ``album_id`` / parsed ``config`` / ``reference_dir``) at construction
    and exposes a no-argument ``run``, so it runs via ``to_task().submit(...)`` like
    the fan-out stages — no external task wrapper. The Immich HTTP layer is injected
    as a :class:`LeadFaceProvider` (the real
    :class:`~album.immich.client.ImmichClient` satisfies it) so tests pass a fake
    without a network; ``sleep`` is injected so the async-detection wait doesn't
    actually block tests. Unlike a ``Stage``, ``run`` commits explicitly (there is
    no ``Album``-loading envelope to own the commit).
    """

    task_name: ClassVar[str] = "resolve-leads"

    def __init__(
        self,
        provider: LeadFaceProvider,
        album_id: int,
        config: LeadFacesYAML | None,
        reference_dir: Path,
        session_factory: SessionFactory,
        *,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._album_id = album_id
        self._config = config
        self._reference_dir = reference_dir
        self._session_factory = session_factory
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s
        self._sleep = sleep

    def run(self) -> LeadResult:
        """Open a session, resolve leads, commit. No ``lead_faces`` configured ⇒ no-op.

        ``reference_dir`` is the directory ``album.yml`` lives in, used to resolve
        relative ``reference_images``. Idempotent: reference photos are re-uploaded
        each run and Immich content-dedupes them, so a re-run is a no-op unless a
        reference photo actually changed (then the new lead takes effect).
        """
        with self._session_factory() as session:
            result = self._resolve(session)
            session.commit()
            return result

    def _resolve(self, session: Session) -> LeadResult:
        config = self._config
        if config is None:
            return LeadResult()

        album_id = self._album_id
        result = LeadResult()
        person_ids = self._resolve_person_ids(
            album_id,
            config,
            self._reference_dir,
            session,
            result,
        )
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
        # Dispatch on truthiness (matching LeadFacesYAML's exactly-one validation,
        # which also treats an empty list as "not provided") so an explicit `[]`
        # can't shadow the real input.
        if config.person_ids:
            # Named directly — no Immich round-trip, just de-dupe (order-preserving).
            return list(dict.fromkeys(config.person_ids))

        if config.reference_asset_ids:
            # Already in Immich: read each asset's faces (no upload), union them.
            return self._person_ids_from_assets(
                config.reference_asset_ids,
                result,
                fetch=self._provider.get_faces,
            )

        # reference_images: upload each then wait out Immich's async detection per
        # asset, unioning the faces across every reference photo.
        assert config.reference_images  # guaranteed by the caller's dispatch
        asset_ids = self._upload_references(config.reference_images, reference_dir, result)
        return self._person_ids_from_assets(asset_ids, result, fetch=self._await_faces)

    def _person_ids_from_assets(
        self,
        asset_ids: list[str],
        result: LeadResult,
        *,
        fetch: Callable[[str], list[ImmichFace]],
    ) -> list[str]:
        """Union the faces across ``asset_ids`` (``fetch`` reads one asset's faces —
        an immediate ``get_faces`` for already-in-Immich assets, or the async-detection
        poll for freshly uploaded ones) and reduce them to assigned person IDs."""
        faces = [face for asset_id in asset_ids for face in fetch(asset_id)]
        return self._person_ids_from_faces(faces, result)

    def _upload_references(
        self,
        images: list[str],
        reference_dir: Path,
        result: LeadResult,
    ) -> list[str]:
        """Upload each reference photo, returning the Immich asset ids in order.

        Uploaded on **every** run — Immich content-dedupes, so identical bytes
        return the existing asset id (no storage churn) and a *changed* reference
        photo returns a new id, which is what makes swapping a lead via
        ``--lead-face`` actually take effect (no stale local cache to invalidate).
        Raises :class:`LeadFaceResolutionError` naming the first missing file — an
        absolute entry (as the ``--lead-face`` CLI writes) ignores ``reference_dir``."""
        asset_ids: list[str] = []
        for image in images:
            path = reference_dir / image
            if not path.is_file():
                raise LeadFaceResolutionError(
                    f"lead_faces reference image {image!r} not found at {path}",
                )
            asset_ids.append(self._provider.upload_asset(path))
        result.uploaded_reference = True
        return asset_ids

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
                f"{self._poll_timeout_s:g}s — Immich's face-detection job may not have run",
            )
        return faces

    @staticmethod
    def _person_ids_from_faces(faces: list[ImmichFace], result: LeadResult) -> list[str]:
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
        existing = self._existing_persons(album_id, session)
        self._demote_stale_leads(existing, set(person_ids))
        self._promote_leads(album_id, person_ids, existing, session)
        session.flush()

    @staticmethod
    def _existing_persons(album_id: int, session: Session) -> dict[str, Person]:
        """The album's already-synced ``Person`` rows, keyed by Immich person ID."""
        return {
            person.immich_person_id: person
            for person in session.scalars(select(Person).where(Person.album_id == album_id))
        }

    @staticmethod
    def _demote_stale_leads(existing: dict[str, Person], target: set[str]) -> None:
        """Clear ``is_lead`` on any previously-marked lead ``target`` no longer names."""
        for person in existing.values():
            if person.is_lead and person.immich_person_id not in target:
                person.is_lead = False

    @staticmethod
    def _promote_leads(
        album_id: int,
        person_ids: list[str],
        existing: dict[str, Person],
        session: Session,
    ) -> None:
        """Set ``is_lead=True`` on each resolved lead, creating unsynced ones."""
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
