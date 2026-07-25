"""DB-backed concurrency test for ``pipeline`` (build step 25 — see
``knowledge/foundations/pipeline-execution.md``): the thing the unit wiring
tests in ``test_flow.py`` structurally can't exercise, because an in-memory
SQLite ``Session`` isn't shareable across threads. This runs the **real**
``pipeline()`` end to end against a real Postgres+pgvector testcontainer, with
a real ``session_factory`` bound to that container, so the six grouped tasks
(``resolve-leads``/``quality``/``cluster``/``enhance``/``render``/``caption``)
each open a genuine, independent pooled connection under Prefect's
``ThreadPoolTaskRunner`` — proving read-after-write across threads actually
works (commit-before-fan-out, ``expire_all()``-after-join).

Every *external* boundary (Immich, the vision model, the darktable renderer)
is doubled — this is not a live-Immich/Claude/darktable check (that stays
deferred, same as every other live-integration check — see
``knowledge/build/progress.md``). Only the pipeline's own DB/session/threading
behavior is real.

Marked ``integration`` (needs Docker); ``poe test-unit``/``test-fast`` skip it.
Reuses the container/migration scaffolding from
``tests/album/db/test_pgvector_integration.py`` via ``tests/pgvector_support.py``
(quality-practices.md §2.4).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from prefect.testing.utilities import prefect_test_harness
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from album.db.models import Album, Photo
from album.db.similarity import PgVectorSimilarity
from album.flow import pipeline
from album.immich.client import ImmichAsset, ImmichFace
from alembic import command
from tests.doubles import ConstantAesthetic, ConstantEmbedder, RecordingCaptioner
from tests.pgvector_support import PGVECTOR_IMAGE
from tests.pgvector_support import alembic_config as _alembic_config
from tests.pgvector_support import database_url as _database_url

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _prefect_test_db() -> Iterator[None]:
    """Run against a temporary, isolated Prefect database (its own
    ``ThreadPoolTaskRunner``), not a real server or the developer's ``~/.prefect``."""
    with prefect_test_harness():
        yield


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    """A Postgres+pgvector container migrated to head via alembic — module-scoped
    (expensive), mirroring ``test_pgvector_integration.py``'s ``migrated_session``."""
    with PostgresContainer(PGVECTOR_IMAGE, driver="psycopg") as pg:
        url = pg.get_connection_url()
        with _database_url(url):
            command.upgrade(_alembic_config(), "head")
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


class _FakeImmich:
    """One double satisfying every stage's client protocol the real pipeline
    needs (``AssetSource``, ``LeadFaceProvider``, ``ThumbnailSource``,
    ``OriginalSource``) — everything except the DB/session/threading is fake,
    since that's the one thing this test is proving."""

    def __init__(self, assets: list[ImmichAsset], *, image_bytes: bytes) -> None:
        self._assets = assets
        self._image_bytes = image_bytes
        self.closed = False

    def get_album_assets(self, immich_album_id: str) -> list[ImmichAsset]:
        return self._assets

    def get_faces(self, asset_id: str) -> list[ImmichFace]:
        return []

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        return self._image_bytes

    def get_original(self, asset_id: str) -> bytes:
        return self._image_bytes

    def upload_asset(self, path: Path) -> str:
        raise AssertionError("no lead_faces config on disk — upload_asset should never be called")

    def close(self) -> None:
        self.closed = True


class _FakeRenderer:
    """Writes real, decodable JPEG bytes at ``output_path`` instead of shelling
    out to darktable-cli — ``assemble`` embeds ``rendered_path`` via WeasyPrint
    for real, so it needs a real image file, not a placeholder blob."""

    def __init__(self, output_bytes: bytes) -> None:
        self._output_bytes = output_bytes

    def render(self, input_path: Path, xmp_path: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self._output_bytes)


def _asset(asset_id: str, filename: str, captured_at: datetime) -> ImmichAsset:
    return ImmichAsset.model_validate(
        {
            "id": asset_id,
            "originalFileName": filename,
            "exifInfo": {"dateTimeOriginal": captured_at},
        },
    )


class TestPipelineConcurrentPerTaskSessions:
    """Runs `pipeline` end to end with a real `session_factory`: group 1
    (`resolve-leads`/`quality`/`cluster`) and group 2 (`enhance`/`render`/
    `caption`) each open their own pooled connection under the real
    `ThreadPoolTaskRunner`, and the shared session must see what they wrote
    after each `expire_all()`-following join."""

    def test_full_run_with_concurrent_per_task_sessions(
        self,
        migrated_engine: Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        # enhance/render/assemble/the default HtmlPrinter all write to a
        # relative "data/albums/<slug>/..." path (flow.py's `_ALBUMS_DIR`,
        # not injectable through `pipeline()` — see this step's research);
        # chdir keeps every one of those writes inside tmp_path instead of
        # the real repo tree, for the whole synchronous pipeline() call.
        monkeypatch.chdir(tmp_path)

        images_dir = Path(__file__).parent.parent.parent / "fixtures" / "images"
        photo_bytes = (images_dir / "sharp.jpg").read_bytes()

        session_factory = sessionmaker(bind=migrated_engine)
        with Session(migrated_engine) as session:
            album = make_album(slug="step25-concurrency", immich_album_id="immich-step25")
            session.add(album)
            session.commit()
            album_id = album.id
            assert album_id is not None

            # Two assets four hours apart — past cluster's default 90-minute
            # scene gap, so each lands in its own scene and neither is a
            # same-scene near-duplicate of the other (select's near-dup guard
            # only compares within a scene).
            assets = [
                _asset("a1", "a1.jpg", datetime(2026, 5, 2, 9, 0, tzinfo=UTC)),
                _asset("a2", "a2.jpg", datetime(2026, 5, 2, 13, 0, tzinfo=UTC)),
            ]
            client = _FakeImmich(assets, image_bytes=photo_bytes)

            # A call-counting captioner, not just a fixed-output one: this is also
            # the regression guard for the enhance/caption race review caught —
            # enhance is joined before caption is submitted, so the shared analysis
            # must still fire exactly once per selected photo despite concurrent
            # per-task sessions, not once from enhance and again from caption. The
            # same instance backs both stages' analyzers, so its call log is the
            # cross-stage total. (Tone stays the default classical estimator.)
            captioner = RecordingCaptioner()

            pipeline(
                album_slug="step25-concurrency",
                session=session,
                session_factory=session_factory,
                immich_client=client,  # pyright: ignore[reportArgumentType]
                similarity=PgVectorSimilarity(),
                aesthetic=ConstantAesthetic(),
                embedder=ConstantEmbedder(),
                renderer=_FakeRenderer(photo_bytes),
                captioner=captioner,
                # printer left at its default (HtmlPrinter) — also a real,
                # relative-path write, kept inside tmp_path by the chdir above.
            )

            session.expire_all()  # the shared session's identity map must not
            # be trusted after the pipeline's own fan-out — re-read for real.
            photos = {
                photo.filename: photo
                for photo in session.scalars(select(Photo).where(Photo.album_id == album_id))
            }
            assert set(photos) == {"a1.jpg", "a2.jpg"}

            for photo in photos.values():
                # group 1 (resolve-leads/quality/cluster) — written on its own
                # session, must be visible on the shared session after the
                # join + expire_all(), before `select` ran.
                assert photo.blur_score is not None
                assert photo.aesthetic_score == 0.5
                assert photo.clip_embedding is not None
                assert photo.scene_id is not None
                # select (shared session)
                assert photo.selected is True
                # group 2 (enhance/render/caption) — same read-after-write
                # proof, before `assemble` ran.
                assert photo.xmp_written is True
                assert photo.rendered_path is not None
                assert Path(photo.rendered_path).exists()
                assert photo.caption is not None
                # assemble (shared session) laid the photo out onto a page.
                assert photo.page_number is not None

            # Exactly one shared analysis per selected photo — not two
            # (one from `enhance`, one from a concurrently-racing `caption`).
            assert len(captioner.calls) == len(photos)

            refreshed_album = session.get(Album, album_id)
            assert refreshed_album is not None
            # The default HtmlPrinter renders a webpage, not a PDF — assemble only
            # lays out (asserted above), so no master PDF is produced on this path.
            assert refreshed_album.assembled_pdf_path is None
            assert refreshed_album.output_ref is not None  # `fulfill` (HtmlPrinter) ran
