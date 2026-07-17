"""Integration tests against a real Postgres + pgvector container, migrated with
**alembic** (not ``Base.metadata.create_all``) — TestContainers playbook step 21.

Where ``test_select.py`` covers the MMR *logic* on in-memory SQLite with a numpy
``StubSimilarity``, and ``test_similarity.py`` covers the pgvector *query* in
isolation, this file joins them: it runs the whole [`select`](../src/album/stages/select.py)
stage — including the step-18 people/lead coverage — against the **real**
`PgVectorSimilarity` cosine query over a real schema produced by the real
migration chain. It also asserts the migrations create step-17/17b's
faces/people columns and round-trip cleanly (automating the by-hand check done
each schema step).

Marked ``integration`` (needs Docker); ``poe test-unit`` skips it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from album.db.models import Album, Base, Person, Photo
from album.db.session import reuse
from album.db.similarity import PgVectorSimilarity
from album.flow.stages.select import SelectStage
from alembic import command
from tests.pgvector_support import PGVECTOR_IMAGE
from tests.pgvector_support import alembic_config as _alembic_config
from tests.pgvector_support import alembic_head as _alembic_head
from tests.pgvector_support import database_url as _database_url
from tests.pgvector_support import vector as _vector

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def migrated_session() -> Iterator[Session]:
    """A Postgres+pgvector container migrated to head via alembic — module-scoped
    (expensive), so tests use distinct slugs/asset ids to stay independent."""
    with PostgresContainer(PGVECTOR_IMAGE, driver="psycopg") as pg:
        url = pg.get_connection_url()
        with _database_url(url):
            command.upgrade(_alembic_config(), "head")
        engine = create_engine(url)
        try:
            with Session(engine) as session:
                yield session
        finally:
            engine.dispose()


class TestMigratedSchema:
    """The real migration chain produces the current schema, including step 17/17b."""

    def test_migrations_reach_head(self, migrated_session: Session) -> None:
        version = migrated_session.execute(
            text("SELECT version_num FROM alembic_version"),
        ).scalar_one()
        assert version == _alembic_head()

    def test_faces_and_lead_columns_exist(self, migrated_session: Session) -> None:
        rows = migrated_session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'",
            ),
        ).all()
        columns = {(row[0], row[1]) for row in rows}
        # Step 17 (faces) + 17b (lead reference) additions, on the real schema.
        assert ("photos", "person_ids") in columns
        assert ("photos", "face_count") in columns
        assert ("albums", "lead_reference_asset_id") in columns
        assert any(table == "people" for table, _ in columns)

    def test_no_model_migration_drift(self, migrated_session: Session) -> None:
        # The migrated schema matches the ORM models exactly — automating the
        # by-hand `alembic revision --autogenerate` "empty upgrade" check run each
        # schema step. A model column added without a migration would show here.
        context = MigrationContext.configure(migrated_session.connection())
        diffs = compare_metadata(context, Base.metadata)
        assert diffs == []


class TestSelectAgainstRealPgvector:
    """`select` end-to-end over the real pgvector cosine query (not the stub)."""

    def _by_asset(self, album: Album) -> dict[str, Photo]:
        return {photo.immich_asset_id: photo for photo in album.photos}

    def test_near_duplicate_skipped_by_real_cosine_query(
        self,
        migrated_session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="int-nd", immich_album_id="immich-int-nd")
        best = make_photo(
            album,
            immich_asset_id="nd-best",
            filename="best.jpg",
            scene_id=1,
            aesthetic_score=0.9,
            clip_embedding=_vector((0, 1.0)),
        )
        near = make_photo(
            album,
            immich_asset_id="nd-near",
            filename="near.jpg",
            scene_id=1,
            aesthetic_score=0.8,
            clip_embedding=_vector((0, 0.99), (1, 0.14)),  # cosine ~0.99 to best
        )
        distinct = make_photo(
            album,
            immich_asset_id="nd-distinct",
            filename="distinct.jpg",
            scene_id=1,
            aesthetic_score=0.7,
            clip_embedding=_vector((1, 1.0)),  # orthogonal to best
        )
        album.photos = [best, near, distinct]
        migrated_session.add(album)
        migrated_session.commit()

        result = SelectStage(
            PgVectorSimilarity(),
            album.id,
            reuse(migrated_session),
            threshold=0.9,
        ).run()

        photos = self._by_asset(album)
        assert photos["nd-best"].selected is True
        assert photos["nd-near"].selected is False  # skipped by the REAL cosine query
        assert photos["nd-distinct"].selected is True
        assert result.selected == 2

        # Idempotent over the real query too (resets + recomputes, same result).
        rerun = SelectStage(
            PgVectorSimilarity(),
            album.id,
            reuse(migrated_session),
            threshold=0.9,
        ).run()
        assert rerun == result
        assert {p.immich_asset_id for p in album.photos if p.selected} == {"nd-best", "nd-distinct"}

    def test_lead_coverage_against_real_query_and_person_ids_json(
        self,
        migrated_session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="int-lead", immich_album_id="immich-int-lead")
        nonlead = make_photo(
            album,
            immich_asset_id="ld-nonlead",
            filename="nonlead.jpg",
            scene_id=1,
            aesthetic_score=0.9,
            clip_embedding=_vector((0, 1.0)),
            person_ids=["p-x"],
        )
        lead = make_photo(
            album,
            immich_asset_id="ld-lead",
            filename="lead.jpg",
            scene_id=1,
            aesthetic_score=0.5,
            clip_embedding=_vector((0, 0.99), (1, 0.14)),  # near-dup of the non-lead
            person_ids=["p-lead"],
        )
        album.photos = [nonlead, lead]
        migrated_session.add(album)
        migrated_session.commit()

        # person_ids survives the round-trip through the real Postgres JSON column
        # (not just the ORM cache): read it back with a raw SQL query.
        stored = migrated_session.execute(
            text("SELECT person_ids FROM photos WHERE immich_asset_id = 'ld-lead'"),
        ).scalar_one()
        assert stored == ["p-lead"]

        # Negative control (no lead yet): the second photo is a near-dup of the
        # first per the REAL cosine query, so with no lead status it is dropped.
        control = SelectStage(
            PgVectorSimilarity(),
            album.id,
            reuse(migrated_session),
            threshold=0.9,
        ).run()
        photos = self._by_asset(album)
        assert (photos["ld-nonlead"].selected, photos["ld-lead"].selected) == (True, False)
        assert control.selected == 1

        # Now mark it a lead: lead coverage rescues the very photo the real query
        # just rejected as a near-dup of the non-lead (step-18 over real pgvector).
        album.people.append(Person(immich_person_id="p-lead", is_lead=True))
        migrated_session.commit()

        result = SelectStage(
            PgVectorSimilarity(),
            album.id,
            reuse(migrated_session),
            threshold=0.9,
        ).run()
        photos = self._by_asset(album)
        assert (photos["ld-nonlead"].selected, photos["ld-lead"].selected) == (True, True)
        assert result.selected == 2


class TestMigrationRoundTrip:
    """The full migration chain reverses and re-applies cleanly on real Postgres —
    the by-hand downgrade/upgrade check done each schema step, automated."""

    def test_downgrade_to_base_then_reupgrade_is_clean(self) -> None:
        with PostgresContainer(PGVECTOR_IMAGE, driver="psycopg") as pg:
            url = pg.get_connection_url()
            with _database_url(url):
                config = _alembic_config()
                command.upgrade(config, "head")
                command.downgrade(config, "base")  # reverse every migration
                command.upgrade(config, "head")  # and re-apply from scratch
            engine = create_engine(url)
            try:  # dispose the pool on every path, incl. a failing query
                with engine.connect() as conn:
                    version = conn.execute(
                        text("SELECT version_num FROM alembic_version"),
                    ).scalar_one()
            finally:
                engine.dispose()
        assert version == _alembic_head()
