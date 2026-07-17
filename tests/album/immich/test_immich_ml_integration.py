"""Immich ML integration test — TestContainers playbook step 24.

Every other Immich test doubles the server: ``respx`` for the HTTP contract, a
``FakeImmich`` for behaviour. This one runs the **real** Immich stack — server,
the vectorchord Postgres, valkey, and a **pre-baked** machine-learning image with
the CLIP + face ONNX models already on disk — and drives it through our own
:class:`~album.immich.client.ImmichClient`. It closes three long-standing "verify
on a live instance" deferrals (progress.md Notes) without fetching any ML **model**
from the internet at test time:

- **Real face detection** (steps 17): upload a photo, wait out Immich's async job
  cascade, and assert ``get_faces`` returns a real machine-learning detection —
  the ``?id=`` contract that was only ever exercised against fixtures.
- **Real lead resolution** (step 17b): run the real
  :class:`~album.flow.stages.lead_faces.LeadFaceResolver` through the
  ``reference_image`` path — a genuine ``POST /assets`` upload followed by polling
  real async detection, the exact timing that was deferred — and confirm the
  resolved reference face is machine-learning-sourced.
- **CLIP offline** (open Q1 / quality embeddings): smart search returns the
  uploaded asset, proving the CLIP model loaded and embedded from the baked cache
  with ``HF_HUB_OFFLINE=1`` — no model fetched from the internet.

**Opt-in and heavy.** Guarded by ``IMMICH_ML_INTEGRATION=1`` so it is skipped in
the default ``poe check`` gate (a multi-service stack + minutes of CPU inference;
image size noted in testcontainers.md). Run it with ``uv run poe test-immich-ml``;
the first run performs the one-time, internet-touching setup — baking the ML image
and pre-pulling the stack images (or run ``poe bake-immich-ml`` beforehand) — after
which runs are offline. The face fixture is ``skimage.data.astronaut()`` (a
public-domain NASA image with a clear frontal face), materialised at runtime, so no
personal photo is committed to the repo.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from skimage.data import astronaut
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from album.db.models import Album, Base
from album.definition import LeadFacesYAML
from album.flow.stages.lead_faces import LeadFaceResolver
from album.immich.client import ImmichClient

from .immich_ml_stack import (
    immich_stack,
    ml_image_exists,
    prepare_offline_stack,
    wait_jobs_idle,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("IMMICH_ML_INTEGRATION") != "1",
        reason="opt-in (heavy real Immich stack): use `poe test-immich-ml`",
    ),
]


def _write_face_jpeg(path: Path, *, flip: bool = False) -> None:
    """Materialise the public-domain astronaut face as a JPEG.

    ``flip`` mirrors the image so its bytes differ from the un-flipped copy —
    Immich content-dedupes uploads, so a distinct file forces a genuinely new
    asset and a fresh async face-detection run (what the lead-resolver test wants).
    """
    pixels = astronaut()
    if flip:
        pixels = np.fliplr(pixels)
    Image.fromarray(pixels).save(path, "JPEG", quality=95)


@pytest.fixture(scope="module")
def immich_client() -> Iterator[ImmichClient]:
    """The real Immich stack, wrapped in our client.

    Performs the one-time offline setup (bake + pre-pull) if the ML image is
    absent, then runs the stack for the module and yields a ready client.
    """
    if not ml_image_exists():
        setup = prepare_offline_stack()
        if setup.returncode != 0:
            pytest.fail(f"offline stack setup failed:\n{setup.stdout}\n{setup.stderr}")
    with immich_stack() as (base_url, api_key), ImmichClient(base_url, api_key) as client:
        yield client


@pytest.fixture(scope="module")
def detected_asset_id(immich_client: ImmichClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Upload the face photo once and wait out Immich's async job cascade."""
    photo = tmp_path_factory.mktemp("immich-ml") / "astronaut.jpg"
    _write_face_jpeg(photo)
    asset_id = immich_client.upload_asset(photo)
    wait_jobs_idle(immich_client)
    return asset_id


@pytest.fixture
def sqlite_session_factory() -> Iterator[sessionmaker[Session]]:
    """A StaticPool in-memory SQLite factory (one shared DB across sessions),
    torn down even if the test fails — for the lead resolver's own session."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


class TestImmichMlIntegration:
    def test_face_detection_produces_a_real_face(
        self, immich_client: ImmichClient, detected_asset_id: str
    ) -> None:
        faces = immich_client.get_faces(detected_asset_id)

        assert faces, "Immich detected no faces on the astronaut photo — ML did not run offline"
        assert any(face.source_type == "machine-learning" for face in faces), (
            f"no machine-learning-sourced face among {[f.source_type for f in faces]}"
        )

    def test_lead_resolver_against_real_async_detection(
        self,
        immich_client: ImmichClient,
        tmp_path: Path,
        sqlite_session_factory: sessionmaker[Session],
    ) -> None:
        # A distinct (mirrored) copy so Immich runs detection afresh and the
        # resolver's own upload + poll loop exercises the real async timing (17b).
        reference_dir = tmp_path
        _write_face_jpeg(reference_dir / "lead.jpg", flip=True)
        with sqlite_session_factory() as seed:
            album = Album(slug="ml-it", title="ML IT")
            seed.add(album)
            seed.commit()
            album_id = album.id

        resolver = LeadFaceResolver(
            provider=immich_client,
            album_id=album_id,
            config=LeadFacesYAML(reference_image="lead.jpg"),
            reference_dir=reference_dir,
            session_factory=sqlite_session_factory,
            poll_timeout_s=300.0,
        )
        result = resolver.run()

        assert result.uploaded_reference, "resolver did not upload the reference image"
        # A real face was seen: either assigned to a Person or surfaced as unassigned.
        assert result.lead_person_ids or result.unassigned_reference_faces >= 1, (
            "resolver saw no faces on the reference — real async detection did not complete"
        )
        # And it was machine-learning-detected, not an EXIF/manual face.
        with sqlite_session_factory() as check:
            album = check.get(Album, album_id)
            assert album is not None
            reference_asset_id = album.lead_reference_asset_id
        assert reference_asset_id is not None, "resolver did not cache the reference asset id"
        reference_faces = immich_client.get_faces(reference_asset_id)
        assert any(face.source_type == "machine-learning" for face in reference_faces), (
            "the resolved reference face was not machine-learning-detected"
        )

    def test_smart_search_runs_clip_offline(
        self, immich_client: ImmichClient, detected_asset_id: str
    ) -> None:
        # Smart search is the only CLIP-observable behaviour over the API (the
        # embedding itself never crosses the wire — immich-api.md open Q1). A hit
        # for a generic query proves the CLIP model embedded the asset offline.
        returned_ids = {asset.id for asset in immich_client.smart_search("a person")}

        assert detected_asset_id in returned_ids, (
            "smart search did not return the uploaded asset — CLIP embedding did not run offline"
        )
