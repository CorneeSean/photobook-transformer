"""Shared pytest fixtures: an in-memory SQLite session and object factories.

The ``make_album`` / ``make_segment`` / ``make_photo`` fixtures are **factory
fixtures** (quality-practices.md §2.3) — each yields a builder callable (from
``tests.factories``) with sensible defaults, so a test requests exactly the
builders it uses and gets consistent, DI-provided construction. During the
class-based test retrofit some files still import the builders directly from
``tests.factories``; both paths resolve to the same callables.
"""

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from album.db.models import Album, Base, Photo, Segment

from . import factories

_REPO_ROOT = Path(__file__).resolve().parent.parent
APP_IMAGE_TAG = "album-integration:latest"


@pytest.fixture(scope="session")
def app_image() -> str:
    """Build the shipped app Docker image once per session; returns its tag.

    Shared by the container-backed integration tests (darktable render, CLI
    entrypoint smoke). The build is idempotent and layer-cached, so a warm run is
    fast; the image is left in place as that cache. Shells out to the ``docker``
    CLI, not docker-py — this sandbox's ``docker-credential-gcloud`` helper crashes
    under Python 3.14, breaking docker-py's build (see testcontainers.md landmine).
    """
    build = subprocess.run(
        ["docker", "build", "-t", APP_IMAGE_TAG, str(_REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.fail(f"app image build failed:\n{build.stdout}\n{build.stderr}")
    return APP_IMAGE_TAG


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session for pure-logic tests.

    ``pgvector`` isn't available in SQLite (see data-model.md), so tests using
    this fixture must not exercise ``Photo.clip_embedding`` queries — only
    creation/relationships. Similarity search itself is covered separately
    against a real Postgres test container (build step 8).
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


@pytest.fixture
def make_album() -> Callable[..., Album]:
    return factories.make_album


@pytest.fixture
def make_segment() -> Callable[..., Segment]:
    return factories.make_segment


@pytest.fixture
def make_photo() -> Callable[..., Photo]:
    return factories.make_photo
