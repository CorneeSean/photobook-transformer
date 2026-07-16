"""``flow.py`` — Prefect orchestration for one album, start to finish.

The **only** module that imports Prefect (folder-structure.md rule): every
``stages/*.py`` function is a plain, Prefect-free function, wrapped here as a
``@task``; :func:`pipeline` is the one ``@flow``. Per
[Architecture](/foundations/architecture.md): "Each stage becomes a ``@task``; the
pipeline is one ``@flow`` taking ``album_slug``." Run it by importing and
calling it directly — Prefect 3 has no ``prefect run --param`` (that's
Prefect 1 syntax):

    pipeline(album_slug="honeymoon-2026")

**DAG execution, not a straight line.** The ten stages are really a DAG
with two independent fan-out groups (see
``knowledge/foundations/pipeline-execution.md``): after ``sync``,
``resolve-leads`` ‖ ``quality`` ‖ ``cluster`` all read only ``Photo`` rows
and write disjoint state, joining at ``select``; after ``select``,
``enhance`` runs first, then ``render`` ‖ ``caption`` both run against what
it wrote, joining at ``assemble``. The four remaining stages (``sync``,
``select``, ``assemble``, ``fulfill``) are true chains and stay strictly
sequential.

The four sequential stages run on **one shared ``Session``**, committed
after each stage, exactly as before. The six *grouped* stages take a
``session_factory`` instead and open their own pooled connection each —
a shared ``Session`` isn't thread-safe, and Prefect's default
``ThreadPoolTaskRunner`` runs ``.submit()``ed tasks on real threads. Two
``submit()``/``result()`` fences bound the two groups; within group 2,
``enhance`` is joined *before* ``render``/``caption`` are even submitted, so
both run strictly after ``enhance`` has committed — ``render`` because it
needs enhance's XMP sidecars, and ``caption`` because it routes through the
same idempotent
``PhotoAnalyzer.ensure`` call that ``enhance`` does, keyed off the same five
DB columns (pipeline-execution.md's "neither reads the other" undersells this: running
``caption`` fully concurrently with ``enhance`` let both see pre-``enhance``
state, each fire a redundant vision-model call, and race to commit
conflicting values onto the same row — caught in this step's review, not in
the original design sketch). Once ``enhance`` has committed, ``render``
(slow — shells out to ``darktable-cli``) and ``caption`` (near-instant here,
since ``enhance``'s analysis already populated the caption column; real work
only if ``enhance`` failed for that one photo) run concurrently — ``caption``
disappears into ``render``'s shadow, which is where the real wall-clock win
lands.

Two invariants make the split safe: the shared session is **committed right
before each fan-out** (so worker threads read committed rows), and
**``expire_all()``ed right after each join** (so the sequential stage that
follows re-reads what the workers just wrote, rather than stale
identity-map state from before the fan-out). Concurrency peaks at 3 (group
1); the default ``QueuePool`` (5 + 10 overflow) comfortably covers that, so
no pool sizing change is needed for this step — see
``knowledge/foundations/pipeline-execution.md``'s "What it costs" table.
build-order.md step 14's connection-pool-sizing warning still applies if a
stage's *internal* per-photo loop later becomes Prefect-level fan-out.

Every dependency (DB session, Immich client, similarity search, quality
scorer/embedder, the vision model, the renderer, the output ``Printer``) is
injectable via keyword arguments, defaulting to the real thing built from
``config.py`` (the ``Printer`` defaults to :class:`~album.printers.html.HtmlPrinter`)
— the same DI shape every stage already uses, so
:func:`pipeline` itself is unit-testable (wiring/ordering) without touching
Postgres, Immich, Claude, darktable-cli, or Lulu. See
``tests/album/test_flow.py`` (wiring) and
``tests/album/test_flow_integration.py`` (real concurrent per-task sessions
against a Postgres+pgvector testcontainer).

**Known gap, deferred (not new to this step — see knowledge/build/progress.md's
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
from collections.abc import Callable
from pathlib import Path

from prefect import flow, task
from pydantic_ai.models import Model
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album
from album.db.session import SessionFactory, new_session
from album.db.similarity import PgVectorSimilarity, SimilaritySearch
from album.definition import load_album_definition, load_lead_faces
from album.immich.client import ImmichClient
from album.photo_analyzer import PhotoAnalyzer
from album.printers.base import Printer
from album.printers.html import HtmlPrinter
from album.stages.assemble import AssembleStage
from album.stages.caption import CaptionStage
from album.stages.cluster import ClusterStage
from album.stages.enhance import EnhanceStage
from album.stages.fulfill import FulfillStage
from album.stages.lead_faces import LeadFaceResolver
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
            "knowledge/build/progress.md's notes for build steps 6 and 14"
        )

    def embed(self, image: bytes) -> list[float]:
        raise NotImplementedError(
            "real CLIP embedding (open_clip) isn't wired yet — see "
            "knowledge/build/progress.md's notes for build steps 6 and 14"
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


def _owns_session(fn: Callable[..., None]) -> Callable[..., None]:
    """Wrap a stage task so it runs on its OWN pooled session from a
    ``session_factory``, committing on success — for the fan-out tasks that
    must not share the flow's session across threads
    (pipeline-execution.md §"The change" §1). The four sequential tasks use
    ``_autocommit`` instead, which only adds the commit (they already take
    the flow's shared session, opened/closed by the caller).

    Deliberately **not** ``functools.wraps``-ed: doing so would copy the
    inner function's signature, and Prefect's parameter introspection would
    then bind the ``session_factory`` passed to ``.submit(album_id, factory,
    …)`` against the inner function's ``session`` parameter instead of this
    wrapper's own ``session_factory`` parameter. The explicit outward
    signature below (and no ``@wraps``) is what makes ``.submit()`` bind
    correctly; pass ``name=`` to the outer ``@task`` for a readable run name
    instead, since ``wrapper.__name__`` is now generic.
    """

    def wrapper(
        album_id: int, session_factory: SessionFactory, /, *args: object, **kwargs: object
    ) -> None:
        with session_factory() as session:
            fn(album_id, session, *args, **kwargs)
            session.commit()

    return wrapper


def _autocommit(fn: Callable[..., None]) -> Callable[..., None]:
    """Wrap a stage task that runs on the flow's own shared session, committing
    on success — for the four *sequential* tasks (``sync``, ``select``,
    ``assemble``, ``fulfill``), which all take the shared ``session`` directly
    (opened/closed by :func:`pipeline`, not by the task) and previously
    differed from each other only by repeating the same trailing
    ``session.commit()``. Unlike :func:`_owns_session`, this wrapper's outward
    signature is identical in shape to the inner function's (``album_id``,
    ``session``, …), so there's no ``functools.wraps``/Prefect-introspection
    hazard here — it's still omitted anyway, for the same reason
    :func:`_owns_session` gives one (a generic ``wrapper.__name__``); pass
    ``name=`` to the outer ``@task`` for a readable run name.
    """

    def wrapper(album_id: int, session: Session, /, *args: object, **kwargs: object) -> None:
        fn(album_id, session, *args, **kwargs)
        session.commit()

    return wrapper


@task(name="sync")
@_autocommit
def _sync_task(album_id: int, session: Session, client: ImmichClient) -> None:
    SyncStage(client).run(album_id, session)


@task(name="resolve-leads")
@_owns_session
def _resolve_leads_task(
    album_id: int, session: Session, client: ImmichClient, album_slug: str
) -> None:
    # lead_faces is a resolution input (not persisted Album state), so read it
    # from album.yml here rather than the DB; absent file/block ⇒ a clean no-op.
    album_dir = _ALBUMS_DIR / album_slug
    album_yml = album_dir / "album.yml"
    config = load_lead_faces(album_yml) if album_yml.exists() else None
    LeadFaceResolver(client).run(album_id, config, album_dir, session)


@task(name="quality")
@_owns_session
def _quality_task(
    album_id: int,
    session: Session,
    client: ImmichClient,
    aesthetic: AestheticScorer,
    embedder: Embedder,
) -> None:
    QualityStage(client, aesthetic, embedder=embedder).run(album_id, session)


@task(name="cluster")
@_owns_session
def _cluster_task(album_id: int, session: Session) -> None:
    ClusterStage().run(album_id, session)


@task(name="select")
@_autocommit
def _select_task(album_id: int, session: Session, similarity: SimilaritySearch) -> None:
    SelectStage(similarity).run(album_id, session)


@task(name="enhance")
@_owns_session
def _enhance_task(
    album_id: int, session: Session, client: ImmichClient, model: Model | None
) -> None:
    EnhanceStage(PhotoAnalyzer(client, model=model), client).run(album_id, session)


@task(name="render")
@_owns_session
def _render_task(album_id: int, session: Session, client: ImmichClient, renderer: Renderer) -> None:
    RenderStage(client, renderer).run(album_id, session)


@task(name="caption")
@_owns_session
def _caption_task(
    album_id: int, session: Session, client: ImmichClient, model: Model | None
) -> None:
    CaptionStage(PhotoAnalyzer(client, model=model)).run(album_id, session)


@task(name="assemble")
@_autocommit
def _assemble_task(album_id: int, session: Session) -> None:
    AssembleStage(_ALBUMS_DIR).run(album_id, session)


@task(name="fulfill")
@_autocommit
def _fulfill_task(album_id: int, session: Session, printer: Printer) -> None:
    FulfillStage(printer).run(album_id, session)


def _run_group_1(
    album_id: int,
    factory: SessionFactory,
    client: ImmichClient,
    album_slug: str,
    aesthetic: AestheticScorer,
    embedder: Embedder,
) -> None:
    """resolve-leads ‖ quality ‖ cluster — independent after ``sync``; join at
    ``select``. Each future's own ``session_factory()`` session rolls back
    independently on failure, so one bad task doesn't poison the others; the
    ``.result()`` loop still blocks for all three and re-raises the first
    error before ``select`` runs."""
    futures = [
        _resolve_leads_task.submit(album_id, factory, client, album_slug),
        _quality_task.submit(album_id, factory, client, aesthetic, embedder),
        _cluster_task.submit(album_id, factory),
    ]
    for future in futures:
        future.result()


def _run_group_2(
    album_id: int,
    factory: SessionFactory,
    client: ImmichClient,
    model: Model | None,
    renderer: Renderer,
) -> None:
    """enhance, then render ‖ caption — join at ``assemble``. ``enhance_future``
    is joined *first and explicitly* — not via ``wait_for`` on the other two —
    so both: (a) a real ``enhance`` failure surfaces its own exception rather
    than the masked ``UpstreamTaskError`` a `wait_for`-blocked downstream
    task's ``.result()`` would otherwise raise, and (b) ``render``/``caption``
    are only ever *submitted* once ``enhance`` has already committed, which is
    what actually orders them (not a ``wait_for`` edge — there's nothing left
    to wait for by the time they're submitted). Ordering matters for two
    different reasons: ``render`` needs enhance's XMP sidecars; ``caption``
    routes through the same idempotent ``PhotoAnalyzer.ensure`` call keyed off
    the five columns ``enhance`` writes — letting it start before ``enhance``
    commits would have it independently re-run the same vision call and race
    to commit conflicting values onto the same row (caught in review; not in
    the original design sketch, which treated the two as fully independent).
    """
    enhance_future = _enhance_task.submit(album_id, factory, client, model)
    enhance_future.result()

    render_future = _render_task.submit(album_id, factory, client, renderer)
    caption_future = _caption_task.submit(album_id, factory, client, model)
    for future in (render_future, caption_future):
        future.result()


def _run_pipeline_body(
    album_slug: str,
    db_session: Session,
    factory: SessionFactory,
    client: ImmichClient,
    *,
    similarity: SimilaritySearch | None,
    aesthetic: AestheticScorer | None,
    embedder: Embedder | None,
    renderer: Renderer | None,
    printer: Printer | None,
    model: Model | None,
) -> None:
    """Run every stage for one album, in dependency order: the four sequential
    stages on ``db_session``, the two fan-out groups via ``factory`` — see this
    module's docstring for the DAG shape. Split out of :func:`pipeline` purely
    to keep resource-lifecycle management (session/client open+close, in
    :func:`pipeline`) and stage orchestration (here) as separate, small
    functions — an xenon complexity concern, not a behavioral one.
    """
    album_id = get_or_create_album(db_session, album_slug)
    db_session.commit()

    _sync_task(album_id, db_session, client)
    db_session.commit()  # publish sync's writes before fan-out

    deferred_quality_model = _UnavailableQualityModel()
    _run_group_1(
        album_id,
        factory,
        client,
        album_slug,
        aesthetic if aesthetic is not None else deferred_quality_model,
        embedder if embedder is not None else deferred_quality_model,
    )
    db_session.expire_all()  # re-read what the workers committed
    _select_task(
        album_id,
        db_session,
        similarity if similarity is not None else PgVectorSimilarity(),
    )
    db_session.commit()

    _run_group_2(
        album_id,
        factory,
        client,
        model,
        renderer if renderer is not None else DarktableRenderer(),
    )
    db_session.expire_all()  # re-read render's/caption's committed writes

    _assemble_task(album_id, db_session)
    db_session.commit()

    # HtmlPrinter is the default: a browsable webpage rendered from the
    # on-disk photos, needing no external service or credentials (unlike
    # LuluPrinter, which callers inject when they want a physical order).
    _fulfill_task(album_id, db_session, printer if printer is not None else HtmlPrinter())


@flow(name="photo-album-pipeline", validate_parameters=False)
def pipeline(
    album_slug: str,
    *,
    session: Session | None = None,
    session_factory: SessionFactory | None = None,
    immich_client: ImmichClient | None = None,
    similarity: SimilaritySearch | None = None,
    aesthetic: AestheticScorer | None = None,
    embedder: Embedder | None = None,
    renderer: Renderer | None = None,
    printer: Printer | None = None,
    model: Model | None = None,
) -> None:
    """Run every stage, in dependency order, for one album — see this
    module's docstring for the DAG shape (sequential stages / fan-out
    groups / join fences).

    ``validate_parameters=False``: Prefect otherwise builds a Pydantic
    ``isinstance``-based validator for every parameter, which would reject
    test doubles for these injectable dependencies (and ``ImmichClient``
    itself, since Pydantic's arbitrary-type handling doesn't know about it).
    The stage functions this delegates to do their own type-correct calls;
    Prefect's job here is orchestration, not re-validating what's already
    typed.

    Every dependency defaults to the real thing (built from ``config.py``);
    override any of them for tests or a partial run. ``session_factory``
    defaults to :func:`~album.db.session.new_session` and is what the six
    fan-out tasks use to open their own pooled connection — independent of
    whatever ``session`` was passed in or opened here. Closes/commits what it
    opened, but never what a caller passed in — callers own their own
    session/client lifecycle.
    """
    factory = session_factory if session_factory is not None else new_session
    owns_session = session is None
    db_session = session if session is not None else new_session()
    try:
        # client's construction lives inside this try (not before it) so a
        # failure building it still lets the outer `finally` close db_session
        # — nothing here should leak a resource that came before it.
        owns_client = immich_client is None
        client = immich_client if immich_client is not None else ImmichClient.from_settings()
        try:
            _run_pipeline_body(
                album_slug,
                db_session,
                factory,
                client,
                similarity=similarity,
                aesthetic=aesthetic,
                embedder=embedder,
                renderer=renderer,
                printer=printer,
                model=model,
            )
        finally:
            if owns_client:
                client.close()
    finally:
        if owns_session:
            db_session.close()


__all__ = ["get_or_create_album", "pipeline"]
