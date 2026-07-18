"""``flow/_flow.py`` — Prefect orchestration for one album, start to finish.

This module is the pipeline's **aggregator/integrator**: it builds each stage and
runs it as a Prefect task via the stage's own ``run_as_task()`` /
``to_task().submit()`` (from the ``TaskUnit`` base — see folder-structure.md and
quality-practices.md §1.5), composing them into the DAG. The stage business logic
and its task glue live in ``flow/stages/*.py`` (there are no module-level ``@task``
functions, and no ``flow/_tasks.py`` — each stage's ``run`` opens and commits its own
session); this module only wires the stages together and owns the session/client
lifecycle. :func:`pipeline` is the one ``@flow``, produced by :meth:`Pipeline.flow`.
Run it by importing and calling it directly — Prefect 3 has no
``prefect run --param`` (that's Prefect 1 syntax):

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

The four sequential stages run on **one shared ``Session``**: each is built with a
``reuse(db_session)`` factory and run inline via ``run_as_task()``, and its ``run``
flushes + commits that shared session. The six *grouped* stages are built with the
real ``session_factory`` and **submitted** via ``to_task().submit()`` — each opens
its own pooled connection inside ``run`` on its worker thread (a shared ``Session``
isn't thread-safe, and Prefect's default ``ThreadPoolTaskRunner`` runs
``.submit()``ed tasks on real threads). A stage holds only its factory (no live
session), so the same instance is safe to submit. Two
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
before each fan-out** (by the preceding sequential stage's own ``run`` commit, so
worker threads read committed rows), and **``expire_all()``ed right after each
join** (so the sequential
stage that follows re-reads what the workers just wrote, rather than stale
identity-map state from before the fan-out). Concurrency peaks at 3 (group
1); the default ``QueuePool`` (5 + 10 overflow) comfortably covers that, so
no pool sizing change is needed for this step — see
``knowledge/foundations/pipeline-execution.md``'s "What it costs" table.
build-order.md step 14's connection-pool-sizing warning still applies if a
stage's *internal* per-photo loop later becomes Prefect-level fan-out.

**Class-based wiring.** :class:`Pipeline` holds the injected collaborators (DB
session, Immich client, similarity search, quality scorer/embedder, the tone
estimator + captioner, the renderer, the output ``Printer``), each defaulting to the
real thing built from ``config.py`` (the ``Printer`` defaults to
:class:`~album.printers.html.HtmlPrinter`). It constructs each stage from those
collaborators and hands the constructed stage to the stage's own ``@task`` — so
dependency injection stays in this wiring layer, not the task. The module-level
``pipeline`` is the configured ``@flow`` produced by :meth:`Pipeline.flow`; it
stays unit-testable (wiring/ordering) without touching Postgres, Immich, Claude,
darktable-cli, or Lulu. See ``tests/album/flow/test_flow.py`` (wiring) and
``tests/album/flow/test_flow_integration.py`` (real concurrent per-task sessions
against a Postgres+pgvector testcontainer).

**Aesthetic scorer + CLIP embedder (build step 31).** The real LAION-style
predictor + CLIP embedder (``open_clip`` + ``torch`` + ViT-L/14 weights) is
:class:`~album.analysis.aesthetic.ClipAestheticModel`, wired here behind the same
:class:`AestheticScorer`/:class:`Embedder` seam. :func:`_build_quality_model` is
**capability-gated with a graceful fallback**: with ``quality.scorer == "clip"``
(the default) it builds the real model, but if the ``scoring`` extra or the weights
are absent it logs a loud warning and falls back to :class:`_UnavailableQualityModel`
— whose methods raise, which ``quality()``'s per-photo failure isolation turns into a
logged, counted failure rather than a crash, so a base install still completes with
every ``blur_score``/``aesthetic_score``/``clip_embedding`` left ``NULL``.
``select()`` already has a documented, tested fallback for that (``aesthetic_score is
None`` sorts last; a missing ``clip_embedding`` skips the near-duplicate check).
``quality.scorer == "none"`` opts out explicitly. The model is local, so running it
does not violate the offline-by-default posture (step 29) — the extra stays opt-*in*
only because ``torch`` is heavy (~1.7 GB), not for connectivity.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from prefect import Flow, flow
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.analysis.captioner import Captioner
from album.analysis.tone_estimator import ToneEstimator
from album.config import Settings, get_settings
from album.db.models import Album
from album.db.session import SessionFactory, new_session, reuse
from album.db.similarity import PgVectorSimilarity, SimilaritySearch
from album.definition import AlbumDefinition, LeadFacesDefinition, LeadFacesYAML
from album.flow.photo_analyzer import PhotoAnalyzer
from album.flow.stages.assemble import AssembleStage
from album.flow.stages.caption import CaptionStage
from album.flow.stages.cluster import ClusterStage
from album.flow.stages.enhance import EnhanceStage
from album.flow.stages.fulfill import FulfillStage
from album.flow.stages.lead_faces import LeadFaceResolver
from album.flow.stages.quality import AestheticScorer, Embedder, QualityStage
from album.flow.stages.render import DarktableRenderer, Renderer, RenderStage
from album.flow.stages.select import SelectStage
from album.flow.stages.sync import SyncStage
from album.immich.client import ImmichClient
from album.printers.base import Printer
from album.printers.html import HtmlPrinter

_log = logging.getLogger(__name__)

_ALBUMS_DIR = Path("data/albums")


class _QualityModel(AestheticScorer, Embedder, Protocol):
    """One model backing **both** quality protocols (score + embed) — the shape of both
    the real :class:`~album.analysis.aesthetic.ClipAestheticModel` and the
    :class:`_UnavailableQualityModel` placeholder, injected as both collaborators."""


class _UnavailableQualityModel:
    """The graceful-fallback placeholder for the real aesthetic predictor + CLIP
    embedder (build step 31) — used when the ``scoring`` extra or the weights are
    absent (see :func:`_build_quality_model`). One model would produce both in
    reality, so one placeholder implements both protocols. Raises rather than
    fabricating a score/embedding; ``quality()`` catches it per-photo and leaves the
    columns ``NULL`` (``select()``'s tested fallback then handles the degraded case)."""

    def score(self, image: bytes) -> float:
        raise NotImplementedError(
            "real aesthetic scoring is unavailable — install the 'scoring' extra "
            "(`uv sync --extra scoring`) and run `uv run poe fetch-aesthetic-weights`",
        )

    def embed(self, image: bytes) -> list[float]:
        raise NotImplementedError(
            "real CLIP embedding is unavailable — install the 'scoring' extra "
            "(`uv sync --extra scoring`) and run `uv run poe fetch-aesthetic-weights`",
        )


_SCORERS = frozenset({"clip", "none"})


def _build_quality_model(settings: Settings) -> _QualityModel:
    """The real :class:`~album.analysis.aesthetic.ClipAestheticModel` when
    ``quality.scorer == "clip"`` and torch/open_clip/weights are available; otherwise
    the raising :class:`_UnavailableQualityModel` placeholder (logged). One instance
    backs both protocols — see the module docstring's aesthetic-scorer note.

    The ``scorer`` string is validated first (raises on an unknown value, like
    ``build_captioner`` — so a ``"none"`` typo can't silently enable the heavy model).
    ``"none"`` opts out explicitly; ``"clip"`` tries the real model and, on a
    **capability** failure (extra not installed, weights not fetched, bad
    device/precision), degrades to the placeholder with a loud warning rather than
    crashing — so a base install still runs.
    """
    scorer = settings.quality.scorer
    if scorer not in _SCORERS:
        raise ValueError(f"unknown quality.scorer {scorer!r}; choose from {sorted(_SCORERS)}")
    if scorer == "none":
        return _UnavailableQualityModel()
    try:
        from album.analysis.aesthetic import ClipAestheticModel

        return ClipAestheticModel(settings.quality)
    except Exception:  # ImportError (extra absent) | missing weights | bad device/precision
        _log.warning(
            "real aesthetic scorer unavailable (install the 'scoring' extra + run "
            "`uv run poe fetch-aesthetic-weights`) — curation degrades to NULL scores; "
            "see knowledge/build/step-31-laion-aesthetic-scorer.md",
            exc_info=True,
        )
        return _UnavailableQualityModel()


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
    aesthetic: AestheticScorer | None,
    embedder: Embedder | None,
) -> tuple[AestheticScorer, Embedder]:
    """Resolve the quality collaborators. Both injected (tests) ⇒ returned untouched,
    with **no config read and no model build** — the seam that keeps the wiring tests
    torch-free. Otherwise build **one** shared model from config (the real scorer or
    the placeholder — see :func:`_build_quality_model`) and use it for whichever slot
    wasn't injected, since one model would produce both in reality."""
    if aesthetic is not None and embedder is not None:
        return aesthetic, embedder
    model = _build_quality_model(get_settings())
    return (
        aesthetic if aesthetic is not None else model,
        embedder if embedder is not None else model,
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
        tone_estimator: ToneEstimator | None = None,
        captioner: Captioner | None = None,
        force: bool = False,
    ) -> None:
        self._session = session
        self._session_factory = session_factory if session_factory is not None else new_session
        self._immich_client = immich_client
        self._similarity = _default_similarity(similarity)
        # Resolved lazily in _run_group_1 (not here), so constructing a Pipeline never
        # reads config or loads the ~1.7 GB model — matching the other config-dependent
        # collaborators. Both injected ⇒ no build; see _resolve_quality_deps.
        self._aesthetic = aesthetic
        self._embedder = embedder
        self._quality_deps: tuple[AestheticScorer, Embedder] | None = None
        self._renderer = _default_renderer(renderer)
        self._printer = _default_printer(printer)
        # None → PhotoAnalyzer resolves the offline classical defaults
        # (ClassicalToneEstimator + TemplateCaptioner); inject a ClaudeCaptioner for
        # the opt-in rich-caption mode. Both are passed straight through per fan-out.
        self._tone_estimator = tone_estimator
        self._captioner = captioner
        self._force = force

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
        groups on their own per-thread sessions — see this module's docstring.

        The sequential stages run on the shared ``db_session`` via a ``reuse``
        factory and commit it in ``run`` (each ``run_as_task`` runs inline), so the
        shared session is already committed before each fan-out (the
        commit-before-fan-out invariant); ``expire_all()`` after each join is the
        read-after-write half. Both are load-bearing — see the module docstring.
        """
        album_id = get_or_create_album(db_session, album_slug)
        db_session.commit()  # publish the bootstrapped album row

        # The four sequential stages reuse the shared session (reuse() yields it
        # without closing) and run inline; each flushes + commits it in run().
        shared = reuse(db_session)
        SyncStage(client, album_id, shared).run_as_task()

        self._run_group_1(album_id, client, album_slug)
        db_session.expire_all()  # re-read what group 1's own-session workers committed

        SelectStage(self._similarity, album_id, shared, force=self._force).run_as_task()

        self._run_group_2(album_id, client)
        db_session.expire_all()  # re-read render's/caption's committed writes

        AssembleStage(_ALBUMS_DIR, album_id, shared, force=self._force).run_as_task()
        FulfillStage(self._printer, album_id, shared, force=self._force).run_as_task()

    def _run_group_1(self, album_id: int, client: ImmichClient, album_slug: str) -> None:
        """resolve-leads ‖ quality ‖ cluster — independent after ``sync``; join at
        ``select``. Each submitted unit opens its **own** pooled session (from
        ``session_factory``) on its worker thread and rolls back independently on
        failure, so one bad task doesn't poison the others; the ``.result()`` loop
        still blocks for all three and re-raises the first error before ``select``
        runs. ``lead_faces`` config is read from ``album.yml`` here (the wiring layer
        knows the slug) and passed to the resolver. Each unit holds only the factory
        (no live session), so it is safe to ``.submit()`` to a worker thread."""
        factory = self._session_factory
        album_dir = _ALBUMS_DIR / album_slug
        config = _load_lead_faces(album_dir)
        aesthetic, embedder = self._resolved_quality()
        futures = [
            LeadFaceResolver(client, album_id, config, album_dir, factory).to_task().submit(),
            QualityStage(
                client,
                aesthetic,
                album_id,
                factory,
                embedder=embedder,
                force=self._force,
            )
            .to_task()
            .submit(),
            ClusterStage(album_id, factory, force=self._force).to_task().submit(),
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
        enhance_future = (
            EnhanceStage(
                self._build_analyzer(client),
                client,
                album_id,
                factory,
                force=self._force,
            )
            .to_task()
            .submit()
        )
        enhance_future.result()

        render_future = (
            RenderStage(client, self._renderer, album_id, factory, force=self._force)
            .to_task()
            .submit()
        )
        # Caption carries the same global force. It's what re-captions the
        # *unclustered* selected photos enhance skips (scene_id is None): without it
        # a `--force --captioner X` A/B run would leave those with their stale
        # caption. The cost: for a *clustered* photo this repeats the shared analysis
        # a second time (enhance, joined above, already re-ran it under force) — cheap
        # for the offline TemplateCaptioner, but a second thumbnail fetch + a second
        # paid call for ClaudeCaptioner. So `--force --captioner claude` on an
        # already-run album is ~2× the API cost on clustered photos; prefer a fresh
        # slug for a large online A/B. Under normal (non-force) runs caption still
        # skips (the DB idempotency gate), so the one-analysis-per-photo invariant
        # holds where it matters. See progress.md's step-29 note.
        caption_future = (
            CaptionStage(self._build_analyzer(client), album_id, factory, force=self._force)
            .to_task()
            .submit()
        )
        for future in (render_future, caption_future):
            future.result()

    def _resolved_quality(self) -> tuple[AestheticScorer, Embedder]:
        """The quality scorer + embedder, resolved once and cached. Deferred to here
        (first use, on the main thread before the group-1 fan-out) rather than
        construction, so building a Pipeline stays config-free and torch-free; the one
        shared model is then handed to :class:`QualityStage` as both collaborators."""
        if self._quality_deps is None:
            self._quality_deps = _resolve_quality_deps(self._aesthetic, self._embedder)
        return self._quality_deps

    def _build_analyzer(self, client: ImmichClient) -> PhotoAnalyzer:
        """A ``PhotoAnalyzer`` wrapping the Immich client as the thumbnail source and
        the pipeline's tone estimator + captioner (``None`` ⇒ the offline defaults)."""
        return PhotoAnalyzer(
            client,
            tone_estimator=self._tone_estimator,
            captioner=self._captioner,
        )

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
            tone_estimator: ToneEstimator | None = None,
            captioner: Captioner | None = None,
            force: bool = False,
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
                tone_estimator=tone_estimator,
                captioner=captioner,
                force=force,
            ).run(album_slug)

        return pipeline


pipeline = Pipeline.flow()

__all__ = ["Pipeline", "get_or_create_album", "pipeline"]
