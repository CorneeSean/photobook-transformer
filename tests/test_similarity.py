"""Tests for :class:`~album.db.similarity.PgVectorSimilarity`'s real pgvector
query, against a Postgres test container.

Deliberately separate and slow (build step 8): the MMR *selection logic* is
covered on in-memory SQLite with a stub in test_select.py; this file covers only
the pgvector cosine-distance query itself, which SQLite can't run at all
(data-model.md).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from album.db.models import Album, Base, Photo
from album.db.similarity import PgVectorSimilarity

from .pgvector_support import PGVECTOR_IMAGE
from .pgvector_support import vector as _vector


@pytest.fixture(scope="module")
def pg_session() -> Iterator[Session]:
    """A shared Postgres+pgvector container for the whole file — expensive, so
    module-scoped and read-mostly (quality-practices.md §2.3)."""
    with PostgresContainer(PGVECTOR_IMAGE, driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(engine)
        with Session(engine) as db_session:
            yield db_session
        engine.dispose()


@pytest.mark.integration
class TestPgVectorSimilarity:
    """`PgVectorSimilarity.max_similarity`: the real indexed cosine-distance query."""

    def test_uses_real_cosine_distance(
        self,
        pg_session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        identical_a = _vector((0, 1.0))
        identical_b = _vector((0, 0.5))  # same direction as identical_a -> cosine sim 1.0
        orthogonal = _vector((1, 1.0))
        p1 = make_photo(album, immich_asset_id="p1", filename="p1.jpg", clip_embedding=identical_a)
        p2 = make_photo(album, immich_asset_id="p2", filename="p2.jpg", clip_embedding=identical_b)
        p3 = make_photo(album, immich_asset_id="p3", filename="p3.jpg", clip_embedding=orthogonal)
        album.photos = [p1, p2, p3]
        pg_session.add(album)
        pg_session.commit()

        similarity = PgVectorSimilarity()

        assert similarity.max_similarity(pg_session, identical_a, [p2.id]) == pytest.approx(
            1.0, abs=1e-6
        )
        assert similarity.max_similarity(pg_session, identical_a, [p3.id]) == pytest.approx(
            0.0, abs=1e-6
        )
        # Highest similarity wins when compared against several already-picked photos.
        assert similarity.max_similarity(pg_session, identical_a, [p3.id, p2.id]) == pytest.approx(
            1.0, abs=1e-6
        )

    def test_empty_against_returns_minimum(self, pg_session: Session) -> None:
        similarity = PgVectorSimilarity()

        assert similarity.max_similarity(pg_session, _vector((0, 1.0)), []) == -1.0

    def test_none_embedding_returns_minimum(
        self,
        pg_session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="none-embedding", immich_album_id="immich-none-embedding")
        p1 = make_photo(
            album, immich_asset_id="solo", filename="solo.jpg", clip_embedding=_vector((0, 1.0))
        )
        album.photos = [p1]
        pg_session.add(album)
        pg_session.commit()

        similarity = PgVectorSimilarity()

        assert similarity.max_similarity(pg_session, None, [p1.id]) == -1.0

    def test_ignores_against_photos_without_embedding(
        self,
        pg_session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # A photo already picked but missing its own embedding (nullable — see
        # data-model.md) must be skipped, not crash the query with a SQL NULL distance.
        album = make_album(slug="null-against", immich_album_id="immich-null-against")
        no_embedding = make_photo(
            album, immich_asset_id="no-embedding", filename="no-embedding.jpg", clip_embedding=None
        )
        has_embedding = make_photo(
            album,
            immich_asset_id="has-embedding",
            filename="has-embedding.jpg",
            clip_embedding=_vector((0, 0.5)),
        )
        album.photos = [no_embedding, has_embedding]
        pg_session.add(album)
        pg_session.commit()

        similarity = PgVectorSimilarity()

        result = similarity.max_similarity(
            pg_session, _vector((0, 1.0)), [no_embedding.id, has_embedding.id]
        )

        assert result == pytest.approx(1.0, abs=1e-6)
