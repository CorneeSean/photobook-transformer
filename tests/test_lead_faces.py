"""Tests for :class:`~album.stages.lead_faces.LeadFaceResolver`: album.yml's
priority subjects → Immich person IDs → ``Person.is_lead``, over in-memory SQLite.

The Immich HTTP layer is stubbed by a fake ``LeadFaceProvider``; ``sleep`` is
injected as a no-op so the async-detection poll never actually waits.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Person
from album.definition import LeadFacesYAML
from album.immich.client import ImmichFace
from album.stages.lead_faces import LeadFaceResolutionError, LeadFaceResolver


def _face(face_id: str, person_id: str | None = None) -> ImmichFace:
    person = {"id": person_id, "name": person_id} if person_id is not None else None
    payload = {"id": face_id, "sourceType": "machine-learning", "person": person}
    return ImmichFace.model_validate(payload)


class FakeLeadProvider:
    """In-memory :class:`~album.stages.lead_faces.LeadFaceProvider` for tests.

    ``faces`` is a *sequence* of successive ``get_faces`` responses (to model
    Immich's async detection: e.g. ``[[], [], [face]]``); the last entry repeats
    once exhausted. ``faces_by_asset`` maps an asset id to a single response (for
    the already-in-Immich reference_asset_id path).
    """

    def __init__(
        self,
        *,
        upload_id: str = "ref-asset-1",
        faces: list[list[ImmichFace]] | None = None,
        faces_by_asset: dict[str, list[ImmichFace]] | None = None,
    ) -> None:
        self._upload_id = upload_id
        self._faces = list(faces) if faces is not None else None
        self._faces_by_asset = faces_by_asset or {}
        self.upload_calls: list[Path] = []
        self.face_calls: list[str] = []

    def upload_asset(self, path: Path) -> str:
        self.upload_calls.append(path)
        return self._upload_id

    def get_faces(self, asset_id: str) -> list[ImmichFace]:
        self.face_calls.append(asset_id)
        if self._faces is not None:
            return self._faces.pop(0) if len(self._faces) > 1 else self._faces[0]
        return self._faces_by_asset.get(asset_id, [])


class TestLeadFaceResolver:
    """`LeadFaceResolver`: the three input forms, async wait, idempotency, warnings."""

    def _album(self, session: Session, make_album: Callable[..., Album], **kw: object) -> int:
        album = make_album(immich_album_id="immich-abc", **kw)
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def _resolver(self, provider: FakeLeadProvider, **kw: object) -> LeadFaceResolver:
        # Small timeout + no-op sleep so the poll loop is fast and silent in tests.
        return LeadFaceResolver(
            provider, poll_interval_s=2.0, poll_timeout_s=6.0, sleep=lambda _s: None, **kw
        )

    def _leads(self, session: Session, album_id: int) -> dict[str, bool]:
        return {
            p.immich_person_id: p.is_lead
            for p in session.scalars(select(Person).where(Person.album_id == album_id))
        }

    def test_no_lead_faces_is_noop(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._album(session, make_album)
        provider = FakeLeadProvider()

        result = self._resolver(provider).run(album_id, None, Path("."), session)

        assert result.lead_person_ids == []
        assert provider.upload_calls == [] and provider.face_calls == []
        assert self._leads(session, album_id) == {}

    def test_person_ids_form_marks_leads_no_immich_call(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._album(session, make_album)
        # p-alex was already synced (is_lead False); p-sam hasn't been seen yet.
        session.add(Person(album_id=album_id, immich_person_id="p-alex", name="Alex"))
        session.commit()
        config = LeadFacesYAML(person_ids=["p-alex", "p-sam"])

        result = self._resolver(FakeLeadProvider()).run(album_id, config, Path("."), session)

        assert result.lead_person_ids == ["p-alex", "p-sam"]
        # Existing row flipped to lead; the unseen lead was created as a lead.
        assert self._leads(session, album_id) == {"p-alex": True, "p-sam": True}

    def test_reference_asset_id_reads_faces_without_upload(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._album(session, make_album)
        provider = FakeLeadProvider(
            faces_by_asset={"asset-42": [_face("f1", "p-alex"), _face("f2", "p-sam")]}
        )
        config = LeadFacesYAML(reference_asset_id="asset-42")

        result = self._resolver(provider).run(album_id, config, Path("."), session)

        assert provider.upload_calls == []
        assert provider.face_calls == ["asset-42"]
        assert result.lead_person_ids == ["p-alex", "p-sam"]
        assert self._leads(session, album_id) == {"p-alex": True, "p-sam": True}

    def test_reference_image_uploads_then_polls_until_detected(
        self, session: Session, make_album: Callable[..., Album], tmp_path: Path
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        # Detection is async: two empty polls, then the faces appear.
        provider = FakeLeadProvider(upload_id="ref-1", faces=[[], [], [_face("f1", "p-alex")]])
        config = LeadFacesYAML(reference_image="leads.jpg")

        result = self._resolver(provider).run(album_id, config, tmp_path, session)

        assert provider.upload_calls == [tmp_path / "leads.jpg"]
        # It actually polled through the two empty responses before the faces
        # appeared — not resolving prematurely on the first (empty) call.
        assert provider.face_calls == ["ref-1", "ref-1", "ref-1"]
        assert result.uploaded_reference is True
        assert result.lead_person_ids == ["p-alex"]
        # The uploaded asset id is cached so a re-run won't re-upload.
        assert session.get(Album, album_id).lead_reference_asset_id == "ref-1"  # type: ignore[union-attr]
        assert self._leads(session, album_id) == {"p-alex": True}

    def test_reference_image_reuses_cached_asset_id(
        self, session: Session, make_album: Callable[..., Album], tmp_path: Path
    ) -> None:
        album_id = self._album(session, make_album, lead_reference_asset_id="cached-1")
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(faces=[[_face("f1", "p-alex")]])
        config = LeadFacesYAML(reference_image="leads.jpg")

        result = self._resolver(provider).run(album_id, config, tmp_path, session)

        # Cached — no second upload; faces read against the cached asset id.
        assert provider.upload_calls == []
        assert provider.face_calls == ["cached-1"]
        assert result.uploaded_reference is False
        assert result.lead_person_ids == ["p-alex"]

    def test_reference_image_rerun_does_not_reupload(
        self, session: Session, make_album: Callable[..., Album], tmp_path: Path
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(faces=[[_face("f1", "p-alex")]])  # always resolves
        config = LeadFacesYAML(reference_image="leads.jpg")
        resolver = self._resolver(provider)

        resolver.run(album_id, config, tmp_path, session)
        resolver.run(album_id, config, tmp_path, session)  # a genuine second run

        # The reference was uploaded exactly once across both runs (cached in between).
        assert len(provider.upload_calls) == 1
        assert self._leads(session, album_id) == {"p-alex": True}

    def test_stale_leads_are_cleared_when_config_changes(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._album(session, make_album)
        resolver = self._resolver(FakeLeadProvider())

        resolver.run(album_id, LeadFacesYAML(person_ids=["p-a", "p-b"]), Path("."), session)
        assert self._leads(session, album_id) == {"p-a": True, "p-b": True}

        # album.yml edited: p-b was a mistake, now only p-a (plus a new p-c).
        resolver.run(album_id, LeadFacesYAML(person_ids=["p-a", "p-c"]), Path("."), session)

        # p-b is demoted (no longer named); the lead set exactly matches the new config.
        assert self._leads(session, album_id) == {"p-a": True, "p-b": False, "p-c": True}

    def test_reference_image_timeout_raises(
        self, session: Session, make_album: Callable[..., Album], tmp_path: Path
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(faces=[[]])  # detection never produces a face
        config = LeadFacesYAML(reference_image="leads.jpg")

        with pytest.raises(LeadFaceResolutionError, match="no faces detected"):
            self._resolver(provider).run(album_id, config, tmp_path, session)

    def test_missing_reference_image_raises(
        self, session: Session, make_album: Callable[..., Album], tmp_path: Path
    ) -> None:
        album_id = self._album(session, make_album)
        config = LeadFacesYAML(reference_image="does-not-exist.jpg")

        with pytest.raises(LeadFaceResolutionError, match="not found"):
            self._resolver(FakeLeadProvider()).run(album_id, config, tmp_path, session)

    def test_unassigned_reference_face_warns_not_silently_missed(
        self,
        session: Session,
        make_album: Callable[..., Album],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        album_id = self._album(session, make_album)
        provider = FakeLeadProvider(
            faces_by_asset={"asset-42": [_face("f1", "p-alex"), _face("f2", None)]}
        )
        config = LeadFacesYAML(reference_asset_id="asset-42")

        with caplog.at_level(logging.WARNING):
            result = self._resolver(provider).run(album_id, config, Path("."), session)

        assert result.lead_person_ids == ["p-alex"]  # assigned lead still resolved
        assert result.unassigned_reference_faces == 1
        assert "unassigned" in caplog.text
