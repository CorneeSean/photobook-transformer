"""Shared pytest fixtures: an in-memory SQLite session and object factories.

The ``make_album`` / ``make_segment`` / ``make_photo`` fixtures are **factory
fixtures** (quality-practices.md §2.3) — each yields a builder callable (from
``tests.factories``) with sensible defaults, so a test requests exactly the
builders it uses and gets consistent, DI-provided construction. During the
class-based test retrofit some files still import the builders directly from
``tests.factories``; both paths resolve to the same callables.
"""

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from album.db.models import Album, Base, Photo, Segment

from . import factories


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
