"""``flow/_flow.py`` — Prefect orchestration for one album, start to finish.

This module is the pipeline's **aggregator/integrator**: it imports each stage's
own Prefect ``@task`` (now co-located with the stage it wraps — see
folder-structure.md and quality-practices.md §1.5) and composes them into the DAG.
The per-stage ``@task`` definitions and the stage business logic live in
``flow/stages/*.py``; the session/commit *policy* those tasks run under lives in
``flow/_tasks.py``; this module only wires them together and owns the
session/client lifecycle. :func:`pipeline` is the one ``@flow``, produced by
:meth:`Pipeline.flow`. Run it by importing and calling it directly — Prefect 3 has
no ``prefect run --param`` (that's Prefect 1 syntax):

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
after each stage by their ``@task``'s :func:`~album.flow._tasks.autocommit`
wrapper. The six *grouped* stages take a ``session_factory`` instead (via
:func:`~album.flow._tasks.owns_session`) and open their own pooled connection
each — a shared ``Session`` isn't thread-safe, and Prefect's default
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
before each fan-out** (by the preceding sequential task's own
:func:`~album.flow._tasks.autocommit` commit, so worker threads read committed
rows), and **``expire_all()``ed right after each join** (so the sequential
stage that follows re-reads what the workers just wrote, rather than stale
identity-map state from before the fan-out). Concurrency peaks at 3 (group
1); the default ``QueuePool`` (5 + 10 overflow) comfortably covers that, so
no pool sizing change is needed for this step — see
``knowledge/foundations/pipeline-execution.md``'s "What it costs" table.
build-order.md step 14's connection-pool-sizing warning still applies if a
stage's *internal* per-photo loop later becomes Prefect-level fan-out.

**Class-based wiring.** :class:`Pipeline` holds the injected collaborators (DB
session, Immich client, similarity search, quality scorer/embedder, the vision
model, the renderer, the output ``Printer``), each defaulting to the real thing
built from ``config.py`` (the ``Printer`` defaults to
:class:`~album.printers.html.HtmlPrinter`). It constructs each stage from those
collaborators and hands the constructed stage to the stage's own ``@task`` — so
dependency injection stays in this wiring layer, not the task. The module-level
``pipeline`` is the configured ``@flow`` produced by :meth:`Pipeline.flow`; it
stays unit-testable (wiring/ordering) without touching Postgres, Immich, Claude,
darktable-cli, or Lulu. See ``tests/album/flow/test_flow.py`` (wiring) and
``tests/album/flow/test_flow_integration.py`` (real concurrent per-task sessions
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

from prefect import Flow, flow
from pydantic_ai.models import Model
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album
from album.db.session import SessionFactory, new_session
from album.db.similarity import PgVectorSimilarity, SimilaritySearch
from album.definition import AlbumDefinition, LeadFacesDefinition, LeadFacesYAML
from album.flow.photo_analyzer import PhotoAnalyzer
from album.flow.stages.assemble import AssembleStage, assemble_task
from album.flow.stages.caption import CaptionStage, caption_task
from album.flow.stages.cluster import ClusterStage, cluster_task
from album.flow.stages.enhance import EnhanceStage, enhance_task
from album.flow.stages.fulfill import FulfillStage, fulfill_task
from album.flow.stages.lead_faces import LeadFaceResolver, resolve_leads_task
from album.flow.stages.quality import AestheticScorer, Embedder, QualityStage, quality_task
from album.flow.stages.render import DarktableRenderer, Renderer, RenderStage, render_task
from album.flow.stages.select import SelectStage, select_task
from album.flow.stages.sync import SyncStage, sync_task
from album.immich.client import ImmichClient
from album.printers.base import Printer
from album.printers.html import HtmlPrinter

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

    album = AlbumDefinition().load(album_yml)
    session.add(album)
    session.flush()
    return album.id


def _load_lead_faces(album_dir: Path) -> LeadFacesYAML | None:
    """Parse the album's ``lead_faces`` block from ``album.yml``, or ``None``.

    ``lead_faces`` is a resolution *input* (not persisted ``Album`` state), so it's
    read from ``album.yml`` here in the wiring layer — where the ``album_slug`` is
    known — rather than from the DB; an absent file/block ⇒ a clean no-op resolve.
    """
    album_yml = album_dir / "album.yml"
    if not album_yml.exists():
        return None
    return LeadFacesDefinition().load(album_yml)


def _resolve_quality_deps(
    aesthetic: AestheticScorer | None, embedder: Embedder | None
) -> tuple[AestheticScorer, Embedder]:
    """Fill in whichever of ``aesthetic``/``embedder`` wasn't injected with the
    same shared :class:`_UnavailableQualityModel` placeholder — see this
    module's docstring's "Known gap" note. Shared (not one placeholder each)
    for the same reason the module docstring gives: one model would produce
    both in reality."""
    deferred = _UnavailableQualityModel()
    return (
        aesthetic if aesthetic is not None else deferred,
        embedder if embedder is not None else deferred,
    )


def _default_similarity(similarity: SimilaritySearch | None) -> SimilaritySearch:
    """The injected similarity search, or the real pgvector one built from config."""
    return similarity if similarity is not None else PgVectorSimilarity()


def _default_renderer(renderer: Renderer | None) -> Renderer:
    """The injected renderer, or the real darktable-cli one built from config."""
    return renderer if renderer is not None else DarktableRenderer()


def _default_printer(printer: Printer | None) -> Printer:
    # HtmlPrinter is the default: a browsable webpage rendered from the
    # on-disk photos, needing no external service or credentials (unlike
    # LuluPrinter, which callers inject when they want a physical order).
    return printer if printer is not None else HtmlPrinter()


def _open_or_reuse_session(session: Session | None) -> tuple[Session, bool]:
    """Open a fresh pooled session only if the caller didn't inject one; the
    returned bool says whether :class:`Pipeline` now owns it (and so must
    close it) or not."""
    if session is not None:
        return session, False
    return new_session(), True


def _open_or_reuse_client(immich_client: ImmichClient | None) -> tuple[ImmichClient, bool]:
    """Same shape as :func:`_open_or_reuse_session`, for the Immich client."""
    if immich_client is not None:
        return immich_client, False
    return ImmichClient.from_settings(), True


def _close_if_owned(owns: bool, close: Callable[[], None]) -> None:
    """Close a resource only if :class:`Pipeline` opened it itself — callers
    own the lifecycle of whatever they passed in."""
    if owns:
        close()


class Pipeline:
    """The album pipeline's wiring layer: holds the injected collaborators,
    constructs each stage from them, and composes the DAG.

    Every collaborator is a keyword argument defaulting to the real thing built
    from ``config.py`` — override any for a test or a partial run. ``session`` and
    ``immich_client`` are opened lazily in :meth:`run` (and closed there iff this
    instance opened them); the four pure collaborators are resolved to their
    defaults at construction. The six fan-out tasks use ``session_factory`` (also
    defaulted) to open their own pooled connection per thread, independent of the
    shared ``session``.

    Build the configured ``@flow`` via :meth:`flow` (the module-level ``pipeline``
    is ``Pipeline.flow()``); ``Pipeline(...).run(album_slug)`` runs the DAG
    directly, which is what the flow does under the hood.
    """

    def __init__(
        self,
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
        self._session = session
        self._session_factory = session_factory if session_factory is not None else new_session
        self._immich_client = immich_client
        self._similarity = _default_similarity(similarity)
        self._aesthetic, self._embedder = _resolve_quality_deps(aesthetic, embedder)
        self._renderer = _default_renderer(renderer)
        self._printer = _default_printer(printer)
        self._model = model

    def run(self, album_slug: str) -> None:
        """Run every stage, in dependency order, for one album.

        Opens the shared ``Session`` and the Immich client if they weren't
        injected, runs the DAG, and closes only what it opened itself (callers own
        the lifecycle of whatever they passed in). The client's construction lives
        inside the session's ``try`` so a failure building it still lets the outer
        ``finally`` close the session — nothing leaks a resource that came before
        it.
        """
        db_session, owns_session = _open_or_reuse_session(self._session)
        try:
            client, owns_client = _open_or_reuse_client(self._immich_client)
            try:
                self._execute(album_slug, db_session, client)
            finally:
                _close_if_owned(owns_client, client.close)
        finally:
            _close_if_owned(owns_session, db_session.close)

    def _execute(self, album_slug: str, db_session: Session, client: ImmichClient) -> None:
        """The DAG body: four sequential stages on ``db_session``, two fan-out
        groups via ``session_factory`` — see this module's docstring for the shape.

        Each sequential task commits ``db_session`` via its own ``autocommit``
        wrapper, so the shared session is already committed before each fan-out
        (the commit-before-fan-out invariant); ``expire_all()`` after each join is
        the read-after-write half. Both are load-bearing — see the module docstring.
        """
        album_id = get_or_create_album(db_session, album_slug)
        db_session.commit()  # publish the bootstrapped album row

        sync_task(SyncStage(client), album_id, db_session)  # autocommit publishes sync's writes

        self._run_group_1(album_id, client, album_slug)
        db_session.expire_all()  # re-read what group 1's own-session workers committed

        select_task(SelectStage(self._similarity), album_id, db_session)

        self._run_group_2(album_id, client)
        db_session.expire_all()  # re-read render's/caption's committed writes

        assemble_task(AssembleStage(_ALBUMS_DIR), album_id, db_session)
        fulfill_task(FulfillStage(self._printer), album_id, db_session)

    def _run_group_1(self, album_id: int, client: ImmichClient, album_slug: str) -> None:
        """resolve-leads ‖ quality ‖ cluster — independent after ``sync``; join at
        ``select``. Each future's own ``session_factory()`` session rolls back
        independently on failure, so one bad task doesn't poison the others; the
        ``.result()`` loop still blocks for all three and re-raises the first
        error before ``select`` runs. ``lead_faces`` config is read from
        ``album.yml`` here (the wiring layer knows the slug) and passed through."""
        factory = self._session_factory
        album_dir = _ALBUMS_DIR / album_slug
        config = _load_lead_faces(album_dir)
        futures = [
            resolve_leads_task.submit(
                LeadFaceResolver(client), album_id, factory, config, album_dir
            ),
            quality_task.submit(
                QualityStage(client, self._aesthetic, embedder=self._embedder), album_id, factory
            ),
            cluster_task.submit(ClusterStage(), album_id, factory),
        ]
        for future in futures:
            future.result()

    def _run_group_2(self, album_id: int, client: ImmichClient) -> None:
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
        factory = self._session_factory
        enhance_future = enhance_task.submit(
            EnhanceStage(PhotoAnalyzer(client, model=self._model), client), album_id, factory
        )
        enhance_future.result()

        render_future = render_task.submit(RenderStage(client, self._renderer), album_id, factory)
        caption_future = caption_task.submit(
            CaptionStage(PhotoAnalyzer(client, model=self._model)), album_id, factory
        )
        for future in (render_future, caption_future):
            future.result()

    @classmethod
    def flow(cls) -> Flow[..., None]:
        """Build the configured Prefect ``@flow`` — the module-level ``pipeline``.

        The flow keeps :func:`pipeline`'s public shape (``album_slug`` plus every
        injectable collaborator as a keyword argument), so ``cli.py`` and the tests
        call it unchanged; it simply constructs a :class:`Pipeline` from those
        arguments and runs it.

        ``validate_parameters=False``: Prefect otherwise builds a Pydantic
        ``isinstance``-based validator for every parameter, which would reject
        test doubles for these injectable dependencies (and ``ImmichClient``
        itself, since Pydantic's arbitrary-type handling doesn't know about it).
        The stages this delegates to do their own type-correct calls; Prefect's job
        here is orchestration, not re-validating what's already typed.
        """

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
            cls(
                session=session,
                session_factory=session_factory,
                immich_client=immich_client,
                similarity=similarity,
                aesthetic=aesthetic,
                embedder=embedder,
                renderer=renderer,
                printer=printer,
                model=model,
            ).run(album_slug)

        return pipeline


pipeline = Pipeline.flow()

__all__ = ["Pipeline", "get_or_create_album", "pipeline"]
