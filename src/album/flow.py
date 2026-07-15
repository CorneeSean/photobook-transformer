"""``flow.py`` — Prefect orchestration for one album, start to finish.

The **only** module that imports Prefect (folder-structure.md rule): every
``stages/*.py`` function is a plain, Prefect-free function, wrapped here as a
``@task``; :func:`pipeline` is the one ``@flow``. Per
[Architecture](/architecture.md): "Each stage becomes a ``@task``; the
pipeline is one ``@flow`` taking ``album_slug``." Run it by importing and
calling it directly — Prefect 3 has no ``prefect run --param`` (that's
Prefect 1 syntax):

    pipeline(album_slug="honeymoon-2026")

**One shared ``Session`` for the whole run**, committed after each stage
(not per photo) — every stage already isolates per-photo failures
internally (``quality.py``/``enhance.py``/etc.'s try/except+continue over
their own photo loop), so per-photo Prefect-level fan-out isn't needed for
what's built so far. This keeps the run to one DB connection rather than
fanning out across ``Photo``-count concurrent tasks; build-order.md step
14's connection-pool-sizing warning applies if/when a stage's *internal*
loop becomes Prefect-level fan-out instead.

Every dependency (DB session, Immich client, similarity search, quality
scorer/embedder, the vision model, the renderer, the output ``Printer``) is
injectable via keyword arguments, defaulting to the real thing built from
``config.py`` (the ``Printer`` defaults to :class:`~album.printers.html.HtmlPrinter`)
— the same DI shape every stage already uses, so
:func:`pipeline` itself is unit-testable (wiring/ordering) without touching
Postgres, Immich, Claude, darktable-cli, or Lulu. See
``tests/test_flow.py``.

**Known gap, deferred (not new to this step — see knowledge/progress.md's
step 6 note):** the real LAION-style aesthetic predictor + CLIP embedder
(``open_clip`` + ``torch`` + pretrained weights) were never wired — a heavy
dependency explicitly deferred at step 6. :class:`_UnavailableQualityModel`
stands in for them here: its methods raise, which ``quality()``'s own
per-photo failure isolation already turns into a logged, counted failure
rather than a crash — so a real run still completes,
just with every photo's ``blur_score``/``aesthetic_score``/``clip_embedding``
left ``NULL``. ``select()`` already has a documented, tested fallback for
that (``aesthetic_score is None`` sorts last; a missing ``clip_embedding``
skips the near-duplicate check) — curation quality degrades, but nothing
crashes. Wiring the real predictor is follow-up work, not this step's job.
"""

from __future__ import annotations

import logging
from pathlib import Path

from prefect import flow, task
from pydantic_ai.models import Model
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album
from album.db.session import new_session
from album.db.similarity import PgVectorSimilarity, SimilaritySearch
from album.definition import load_album_definition
from album.immich.client import ImmichClient
from album.photo_analyzer import PhotoAnalyzer
from album.printers.base import Printer
from album.printers.html import HtmlPrinter
from album.stages.assemble import AssembleStage
from album.stages.caption import CaptionStage
from album.stages.cluster import ClusterStage
from album.stages.enhance import EnhanceStage
from album.stages.fulfill import FulfillStage
from album.stages.quality import AestheticScorer, Embedder, QualityStage
from album.stages.render import DarktableRenderer, Renderer, RenderStage
from album.stages.select import SelectStage
from album.stages.sync import SyncStage

_log = logging.getLogger(__name__)

_ALBUMS_DIR = Path("data/albums")


class _UnavailableQualityModel:
    """Placeholder for the real LAION-style aesthetic predictor + CLIP
    embedder — see this module's docstring. One model would produce both
    (same reasoning as the shared vision call in ``agents/analysis.py``), so
    one placeholder implements both protocols. Raises rather than
    fabricating a score/embedding; ``quality()`` catches it per-photo."""

    def score(self, image: bytes) -> float:
        raise NotImplementedError(
            "real aesthetic scoring (LAION/open_clip) isn't wired yet — see "
            "knowledge/progress.md's notes for build steps 6 and 14"
        )

    def embed(self, image: bytes) -> list[float]:
        raise NotImplementedError(
            "real CLIP embedding (open_clip) isn't wired yet — see "
            "knowledge/progress.md's notes for build steps 6 and 14"
        )


def get_or_create_album(session: Session, album_slug: str, albums_dir: Path = _ALBUMS_DIR) -> int:
    """Idempotent album bootstrap from ``<albums_dir>/<album_slug>/album.yml``.

    If an ``Album`` with this slug already exists, its id is reused as-is —
    ``album.yml`` is only consulted the first time a slug is seen. This is a
    resumable **batch** pipeline, not a continuous reconciler: re-importing
    ``album.yml`` on every run could silently reshuffle ``Segment.position``
    or drop/rename segments out from under already-processed photos'
    ``segment_id`` foreign keys. A changed ``album.yml`` for an
    already-imported slug needs deliberate handling, not an automatic
    re-import — out of this step's scope.
    """
    existing_id = session.scalar(sa_select(Album.id).where(Album.slug == album_slug))
    if existing_id is not None:
        return existing_id

    album_yml = albums_dir / album_slug / "album.yml"
    if not album_yml.exists():
        raise FileNotFoundError(f"no album.yml at {album_yml}")

    album = load_album_definition(album_yml)
    session.add(album)
    session.flush()
    return album.id


@task(name="sync")
def _sync_task(album_id: int, session: Session, client: ImmichClient) -> None:
    SyncStage(client).run(album_id, session)
    session.commit()


@task(name="quality")
def _quality_task(
    album_id: int,
    session: Session,
    client: ImmichClient,
    aesthetic: AestheticScorer,
    embedder: Embedder,
) -> None:
    QualityStage(client, aesthetic, embedder=embedder).run(album_id, session)
    session.commit()


@task(name="cluster")
def _cluster_task(album_id: int, session: Session) -> None:
    ClusterStage().run(album_id, session)
    session.commit()


@task(name="select")
def _select_task(album_id: int, session: Session, similarity: SimilaritySearch) -> None:
    SelectStage(similarity).run(album_id, session)
    session.commit()


@task(name="enhance")
def _enhance_task(
    album_id: int, session: Session, client: ImmichClient, model: Model | None
) -> None:
    EnhanceStage(PhotoAnalyzer(client, model=model), client).run(album_id, session)
    session.commit()


@task(name="render")
def _render_task(album_id: int, session: Session, client: ImmichClient, renderer: Renderer) -> None:
    RenderStage(client, renderer).run(album_id, session)
    session.commit()


@task(name="caption")
def _caption_task(
    album_id: int, session: Session, client: ImmichClient, model: Model | None
) -> None:
    CaptionStage(PhotoAnalyzer(client, model=model)).run(album_id, session)
    session.commit()


@task(name="assemble")
def _assemble_task(album_id: int, session: Session) -> None:
    AssembleStage(_ALBUMS_DIR).run(album_id, session)
    session.commit()


@task(name="fulfill")
def _fulfill_task(album_id: int, session: Session, printer: Printer) -> None:
    FulfillStage(printer).run(album_id, session)
    session.commit()


@flow(name="photo-album-pipeline", validate_parameters=False)
def pipeline(
    album_slug: str,
    *,
    session: Session | None = None,
    immich_client: ImmichClient | None = None,
    similarity: SimilaritySearch | None = None,
    aesthetic: AestheticScorer | None = None,
    embedder: Embedder | None = None,
    renderer: Renderer | None = None,
    printer: Printer | None = None,
    model: Model | None = None,
) -> None:
    """Run every stage, in build order, for one album.

    ``validate_parameters=False``: Prefect otherwise builds a Pydantic
    ``isinstance``-based validator for every parameter, which would reject
    test doubles for these injectable dependencies (and ``ImmichClient``
    itself, since Pydantic's arbitrary-type handling doesn't know about it).
    The stage functions this delegates to do their own type-correct calls;
    Prefect's job here is orchestration, not re-validating what's already
    typed.

    Every dependency defaults to the real thing (built from ``config.py``);
    override any of them for tests or a partial run. Closes/commits what it
    opened, but never what a caller passed in — callers own their own
    session/client lifecycle.
    """
    owns_session = session is None
    db_session = session if session is not None else new_session()
    try:
        # client's construction lives inside this try (not before it) so a
        # failure building it still lets the outer `finally` close db_session
        # — nothing here should leak a resource that came before it.
        owns_client = immich_client is None
        client = immich_client if immich_client is not None else ImmichClient.from_settings()
        try:
            album_id = get_or_create_album(db_session, album_slug)
            db_session.commit()

            _sync_task(album_id, db_session, client)
            deferred_quality_model = _UnavailableQualityModel()
            _quality_task(
                album_id,
                db_session,
                client,
                aesthetic if aesthetic is not None else deferred_quality_model,
                embedder if embedder is not None else deferred_quality_model,
            )
            _cluster_task(album_id, db_session)
            _select_task(
                album_id,
                db_session,
                similarity if similarity is not None else PgVectorSimilarity(),
            )
            _enhance_task(album_id, db_session, client, model)
            _render_task(
                album_id,
                db_session,
                client,
                renderer if renderer is not None else DarktableRenderer(),
            )
            _caption_task(album_id, db_session, client, model)
            _assemble_task(album_id, db_session)

            # HtmlPrinter is the default: a browsable webpage rendered from the
            # on-disk photos, needing no external service or credentials (unlike
            # LuluPrinter, which callers inject when they want a physical order).
            _fulfill_task(album_id, db_session, printer if printer is not None else HtmlPrinter())
        finally:
            if owns_client:
                client.close()
    finally:
        if owns_session:
            db_session.close()


__all__ = ["get_or_create_album", "pipeline"]
