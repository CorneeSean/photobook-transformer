"""Tests for :class:`~album.flow.stages.lead_faces.LeadFaceResolver`: album.yml's
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
from album.db.session import reuse
from album.definition import LeadFacesYAML
from album.flow.stages.lead_faces import LeadFaceResolutionError, LeadFaceResolver
from album.immich.client import ImmichFace


def _face(face_id: str, person_id: str | None = None) -> ImmichFace:
    person = {"id": person_id, "name": person_id} if person_id is not None else None
    payload = {"id": face_id, "sourceType": "machine-learning", "person": person}
    return ImmichFace.model_validate(payload)


class FakeLeadProvider:
    """In-memory :class:`~album.flow.stages.lead_faces.LeadFaceProvider` for tests.

    ``faces`` is a *sequence* of successive ``get_faces`` responses (to model
    Immich's async detection: e.g. ``[[], [], [face]]``); the last entry repeats
    once exhausted. ``faces_by_asset`` maps an asset id to a single response (for
    the already-in-Immich reference_asset_ids path).
    """

    def __init__(
        self,
        *,
        upload_id: str = "ref-asset-1",
        upload_ids: list[str] | None = None,
        upload_map: dict[str, str] | None = None,
        faces: list[list[ImmichFace]] | None = None,
        faces_by_asset: dict[str, list[ImmichFace]] | None = None,
    ) -> None:
        self._upload_id = upload_id
        # Successive uploads return successive ids (last repeats once exhausted),
        # so a multi-image reference resolves to distinct asset ids.
        self._upload_ids = list(upload_ids) if upload_ids is not None else None
        # Content-addressed by filename — models Immich's dedup (identical bytes ⇒
        # the same asset id), so re-uploading the same file across runs is stable.
        self._upload_map = upload_map
        self._faces = list(faces) if faces is not None else None
        self._faces_by_asset = faces_by_asset or {}
        self.upload_calls: list[Path] = []
        self.face_calls: list[str] = []

    def upload_asset(self, path: Path) -> str:
        self.upload_calls.append(path)
        if self._upload_map is not None:
            return self._upload_map[path.name]
        if self._upload_ids is not None:
            index = len(self.upload_calls) - 1
            return self._upload_ids[min(index, len(self._upload_ids) - 1)]
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

    def _resolver(
        self,
        provider: FakeLeadProvider,
        album_id: int,
        config: LeadFacesYAML | None,
        reference_dir: Path,
        session: Session,
    ) -> LeadFaceResolver:
        # Small timeout + no-op sleep so the poll loop is fast and silent in tests.
        # The session is reused (not closed) so a test can inspect it after run().
        return LeadFaceResolver(
            provider,
            album_id,
            config,
            reference_dir,
            reuse(session),
            poll_interval_s=2.0,
            poll_timeout_s=6.0,
            sleep=lambda _s: None,
        )

    def _leads(self, session: Session, album_id: int) -> dict[str, bool]:
        return {
            p.immich_person_id: p.is_lead
            for p in session.scalars(select(Person).where(Person.album_id == album_id))
        }

    def test_no_lead_faces_is_noop(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album_id = self._album(session, make_album)
        provider = FakeLeadProvider()

        result = self._resolver(provider, album_id, None, Path("."), session).run()

        assert result.lead_person_ids == []
        assert provider.upload_calls == [] and provider.face_calls == []
        assert self._leads(session, album_id) == {}

    def test_person_ids_form_marks_leads_no_immich_call(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album_id = self._album(session, make_album)
        # p-alex was already synced (is_lead False); p-sam hasn't been seen yet.
        session.add(Person(album_id=album_id, immich_person_id="p-alex", name="Alex"))
        session.commit()
        config = LeadFacesYAML(person_ids=["p-alex", "p-sam"])

        result = self._resolver(FakeLeadProvider(), album_id, config, Path("."), session).run()

        assert result.lead_person_ids == ["p-alex", "p-sam"]
        # Existing row flipped to lead; the unseen lead was created as a lead.
        assert self._leads(session, album_id) == {"p-alex": True, "p-sam": True}

    def test_reference_asset_id_reads_faces_without_upload(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album_id = self._album(session, make_album)
        provider = FakeLeadProvider(
            faces_by_asset={"asset-42": [_face("f1", "p-alex"), _face("f2", "p-sam")]},
        )
        config = LeadFacesYAML(reference_asset_ids=["asset-42"])

        result = self._resolver(provider, album_id, config, Path("."), session).run()

        assert provider.upload_calls == []
        assert provider.face_calls == ["asset-42"]
        assert result.lead_person_ids == ["p-alex", "p-sam"]
        assert self._leads(session, album_id) == {"p-alex": True, "p-sam": True}

    def test_reference_image_uploads_then_polls_until_detected(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        # Detection is async: two empty polls, then the faces appear.
        provider = FakeLeadProvider(upload_id="ref-1", faces=[[], [], [_face("f1", "p-alex")]])
        config = LeadFacesYAML(reference_images=["leads.jpg"])

        result = self._resolver(provider, album_id, config, tmp_path, session).run()

        assert provider.upload_calls == [tmp_path / "leads.jpg"]
        # It actually polled through the two empty responses before the faces
        # appeared — not resolving prematurely on the first (empty) call.
        assert provider.face_calls == ["ref-1", "ref-1", "ref-1"]
        assert result.uploaded_reference is True
        assert result.lead_person_ids == ["p-alex"]
        assert self._leads(session, album_id) == {"p-alex": True}

    def test_reference_image_rerun_reuploads_each_run_leads_stable(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        # Same file both runs ⇒ Immich content-dedup returns the same id (modeled
        # by the fixed upload_id), so re-running is a no-op for the leads.
        provider = FakeLeadProvider(upload_id="ref-1", faces=[[_face("f1", "p-alex")]])
        config = LeadFacesYAML(reference_images=["leads.jpg"])
        resolver = self._resolver(provider, album_id, config, tmp_path, session)

        resolver.run()
        resolver.run()  # a genuine second run

        # No DB cache: the reference is re-uploaded each run (Immich dedups it),
        # and the leads stay put because the deduped id is unchanged.
        assert provider.upload_calls == [tmp_path / "leads.jpg", tmp_path / "leads.jpg"]
        assert self._leads(session, album_id) == {"p-alex": True}

    def test_reference_image_swap_reuploads_and_updates_leads(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        # Regression: swapping the reference photo (same count) must re-resolve the
        # lead — the whole point of `run --lead-face`/`generate --force --lead-face`.
        # A count-based cache silently kept the old lead here.
        album_id = self._album(session, make_album)
        (tmp_path / "alice.jpg").write_bytes(b"jpeg")
        (tmp_path / "bob.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(
            upload_map={"alice.jpg": "ref-alice", "bob.jpg": "ref-bob"},
            faces_by_asset={
                "ref-alice": [_face("f1", "p-alice")],
                "ref-bob": [_face("f2", "p-bob")],
            },
        )

        self._resolver(
            provider,
            album_id,
            LeadFacesYAML(reference_images=["alice.jpg"]),
            tmp_path,
            session,
        ).run()
        assert self._leads(session, album_id) == {"p-alice": True}

        # album.yml/--lead-face now names a *different* single photo.
        result = self._resolver(
            provider,
            album_id,
            LeadFacesYAML(reference_images=["bob.jpg"]),
            tmp_path,
            session,
        ).run()

        # bob was uploaded (not skipped by a same-count cache); alice is demoted.
        assert provider.upload_calls == [tmp_path / "alice.jpg", tmp_path / "bob.jpg"]
        assert result.lead_person_ids == ["p-bob"]
        assert self._leads(session, album_id) == {"p-alice": False, "p-bob": True}

    def test_empty_person_ids_list_falls_through_to_reference_images(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        # Regression: an explicit empty person_ids: [] passes exactly-one validation
        # (it's falsy ⇒ "not provided"); the resolver must dispatch on truthiness too,
        # so it doesn't short-circuit to empty leads and skip the real reference_images.
        album_id = self._album(session, make_album)
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(upload_id="ref-1", faces=[[_face("f1", "p-alex")]])
        config = LeadFacesYAML(person_ids=[], reference_images=["leads.jpg"])

        result = self._resolver(provider, album_id, config, tmp_path, session).run()

        assert provider.upload_calls == [tmp_path / "leads.jpg"]
        assert result.lead_person_ids == ["p-alex"]
        assert self._leads(session, album_id) == {"p-alex": True}

    def test_stale_leads_are_cleared_when_config_changes(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album_id = self._album(session, make_album)
        provider = FakeLeadProvider()

        cfg_ab = LeadFacesYAML(person_ids=["p-a", "p-b"])
        self._resolver(provider, album_id, cfg_ab, Path("."), session).run()
        assert self._leads(session, album_id) == {"p-a": True, "p-b": True}

        # album.yml edited: p-b was a mistake, now only p-a (plus a new p-c).
        cfg_ac = LeadFacesYAML(person_ids=["p-a", "p-c"])
        self._resolver(provider, album_id, cfg_ac, Path("."), session).run()

        # p-b is demoted (no longer named); the lead set exactly matches the new config.
        assert self._leads(session, album_id) == {"p-a": True, "p-b": False, "p-c": True}

    def test_reference_image_timeout_raises(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "leads.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(faces=[[]])  # detection never produces a face
        config = LeadFacesYAML(reference_images=["leads.jpg"])

        with pytest.raises(LeadFaceResolutionError, match="no faces detected"):
            self._resolver(provider, album_id, config, tmp_path, session).run()

    def test_missing_reference_image_raises(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        album_id = self._album(session, make_album)
        config = LeadFacesYAML(reference_images=["does-not-exist.jpg"])

        with pytest.raises(LeadFaceResolutionError, match="not found"):
            self._resolver(FakeLeadProvider(), album_id, config, tmp_path, session).run()

    def test_reference_images_upload_each_and_union_faces(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "alex.jpg").write_bytes(b"jpeg")
        (tmp_path / "sam.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(
            upload_ids=["ref-alex", "ref-sam"],
            faces_by_asset={
                "ref-alex": [_face("f1", "p-alex")],
                "ref-sam": [_face("f2", "p-sam")],
            },
        )
        config = LeadFacesYAML(reference_images=["alex.jpg", "sam.jpg"])

        result = self._resolver(provider, album_id, config, tmp_path, session).run()

        assert provider.upload_calls == [tmp_path / "alex.jpg", tmp_path / "sam.jpg"]
        assert provider.face_calls == ["ref-alex", "ref-sam"]
        # Faces are unioned across both reference photos.
        assert result.lead_person_ids == ["p-alex", "p-sam"]
        assert self._leads(session, album_id) == {"p-alex": True, "p-sam": True}

    def test_reference_images_multi_rerun_reuploads_via_dedup(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        # A genuine populate-then-rerun for the multi-image path: both runs upload
        # both photos (Immich dedups the identical bytes to the same ids), so the
        # leads are stable across runs — no DB cache involved.
        album_id = self._album(session, make_album)
        (tmp_path / "alex.jpg").write_bytes(b"jpeg")
        (tmp_path / "sam.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(
            upload_map={"alex.jpg": "ref-alex", "sam.jpg": "ref-sam"},
            faces_by_asset={
                "ref-alex": [_face("f1", "p-alex")],
                "ref-sam": [_face("f2", "p-sam")],
            },
        )
        config = LeadFacesYAML(reference_images=["alex.jpg", "sam.jpg"])
        resolver = self._resolver(provider, album_id, config, tmp_path, session)

        resolver.run()
        resolver.run()

        assert provider.upload_calls == [
            tmp_path / "alex.jpg",
            tmp_path / "sam.jpg",
            tmp_path / "alex.jpg",
            tmp_path / "sam.jpg",
        ]
        assert self._leads(session, album_id) == {"p-alex": True, "p-sam": True}

    def test_reference_images_count_unassigned_faces_across_images(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "a.jpg").write_bytes(b"jpeg")
        (tmp_path / "b.jpg").write_bytes(b"jpeg")
        provider = FakeLeadProvider(
            upload_ids=["ref-a", "ref-b"],
            faces_by_asset={
                "ref-a": [_face("f1", "p-alex"), _face("f2", None)],
                "ref-b": [_face("f3", None)],
            },
        )
        config = LeadFacesYAML(reference_images=["a.jpg", "b.jpg"])

        with caplog.at_level(logging.WARNING):
            result = self._resolver(provider, album_id, config, tmp_path, session).run()

        assert result.lead_person_ids == ["p-alex"]
        # Unassigned faces are counted across both reference photos, not just one.
        assert result.unassigned_reference_faces == 2
        assert "unassigned" in caplog.text

    def test_missing_reference_images_entry_raises_naming_it(
        self,
        session: Session,
        make_album: Callable[..., Album],
        tmp_path: Path,
    ) -> None:
        album_id = self._album(session, make_album)
        (tmp_path / "here.jpg").write_bytes(b"jpeg")
        config = LeadFacesYAML(reference_images=["here.jpg", "gone.jpg"])

        with pytest.raises(LeadFaceResolutionError, match="gone.jpg"):
            self._resolver(FakeLeadProvider(), album_id, config, tmp_path, session).run()

    def test_unassigned_reference_face_warns_not_silently_missed(
        self,
        session: Session,
        make_album: Callable[..., Album],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        album_id = self._album(session, make_album)
        provider = FakeLeadProvider(
            faces_by_asset={"asset-42": [_face("f1", "p-alex"), _face("f2", None)]},
        )
        config = LeadFacesYAML(reference_asset_ids=["asset-42"])

        with caplog.at_level(logging.WARNING):
            result = self._resolver(provider, album_id, config, Path("."), session).run()

        assert result.lead_person_ids == ["p-alex"]  # assigned lead still resolved
        assert result.unassigned_reference_faces == 1
        assert "unassigned" in caplog.text
