from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from album.db.models import Album, Base, Photo, Segment


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


def make_album(**overrides: object) -> Album:
    defaults: dict[str, object] = {
        "slug": "honeymoon-2026",
        "title": "Our Honeymoon",
    }
    defaults.update(overrides)
    return Album(**defaults)  # pyright: ignore[reportArgumentType]


def make_segment(album: Album, **overrides: object) -> Segment:
    defaults: dict[str, object] = {
        "album": album,
        "position": 1,
        "label": "Day 1",
        "start_at": datetime(2026, 1, 1, tzinfo=UTC),
        "end_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Segment(**defaults)  # pyright: ignore[reportArgumentType]


def make_photo(album: Album, **overrides: object) -> Photo:
    defaults: dict[str, object] = {
        "album": album,
        "immich_asset_id": "asset-1",
        "filename": "IMG_0001.jpg",
    }
    defaults.update(overrides)
    return Photo(**defaults)  # pyright: ignore[reportArgumentType]
