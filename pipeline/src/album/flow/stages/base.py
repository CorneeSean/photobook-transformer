"""The ``TaskUnit`` / ``Stage`` bases shared by every pipeline stage.

A pipeline unit runs as a **named Prefect task** and is constructed on the flow
thread but may execute on a worker thread. The two bases split those concerns:

``TaskUnit`` — the task surface. A subclass sets the class-level ``task_name`` and
implements the no-argument ``run``; ``to_task`` wraps ``run`` as a Prefect ``@task``
carrying that name, ``run_as_task`` runs it inline (a sequential stage on the shared
session), and ``to_task().submit(...)`` submits it to a worker thread (a fan-out
stage). The unit is safe to submit because it holds **no live ``Session``** — only a
``session_factory`` it opens *inside* ``run``, on whatever thread runs it.

``Stage`` — the Template Method for a stage. Its constructor takes the ``album_id``,
a ``session_factory``, and ``force``; ``run`` opens the session from the factory,
resolves the album by ``album_id`` (guarding existence) into ``self._album``, runs
the variant ``_process`` hook, then flushes + commits the session. The load + guard
and the flush + commit are the invariant envelope every stage shared — hoisting them
here is what lets each ``_process`` read ``self._album``/``self._session``/
``self._force`` and drop its own trailing flush, and lets each task drop its commit.

Only the genuinely invariant steps are hoisted. The *idempotency* short-circuit is
deliberately **not** here — it varies structurally across stages — so each
``_process`` keeps its own. ``Stage`` is generic over its result type, so a subclass
keeps its own concrete ``@dataclass`` return — ``class SyncStage(Stage[SyncResult])``
with ``_process() -> SyncResult``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from prefect import Task, task
from sqlalchemy.orm import Session

from album.db.models import Album
from album.db.session import SessionFactory

_log = logging.getLogger(__name__)


class StageTotalFailure(RuntimeError):
    """Raised by :meth:`Stage.run` when every *attempted* item in a stage failed —
    a systemic failure (Immich down, ``darktable-cli`` missing, model unavailable)
    rather than an isolated bad photo. A dedicated subclass so callers can isolate a
    systemic stage failure (e.g. ``cli.runner.run_pipelines`` reports it per album)
    without also swallowing genuine ``RuntimeError`` bugs.
    """


class TaskUnit[ResultT](ABC):
    """A unit of pipeline work that runs as a named Prefect task.

    Subclasses set ``task_name`` (a class attribute) and implement the no-argument
    ``run``. Because a unit holds no live ``Session`` — it opens one from a
    ``session_factory`` *inside* ``run`` — the same instance is safe to hand to
    ``.submit()`` on a worker thread.
    """

    task_name: ClassVar[str]

    @abstractmethod
    def run(self) -> ResultT:
        """Do the unit's work and return its result. Opens its own session."""

    def to_task(self) -> Task[..., ResultT]:
        """This unit's ``run`` as a Prefect ``@task`` named ``task_name``.

        Call it directly (``to_task()()``, via :meth:`run_as_task`) to run inline,
        or ``to_task().submit(...)`` to submit it to a worker thread.
        """
        return task(name=self.task_name)(self.run)

    def run_as_task(self) -> ResultT:
        """Run inline as a Prefect task — a sequential stage on the shared session."""
        return self.to_task()()


class Stage[ResultT](TaskUnit[ResultT]):
    """Template Method base for a pipeline stage.

    Constructed with the ``album_id``, a ``session_factory``, and ``force``; holds no
    live ``Session``. ``run`` (from :class:`TaskUnit`) opens the session from the
    factory, resolves + guards the album into ``self._album``, runs the per-stage
    ``_process`` (which reads ``self._album``/``self._session``/``self._force``), then
    flushes + commits. A subclass forwards the three base arguments via
    ``super().__init__`` after binding its own injected collaborators.
    """

    # Populated by ``run`` inside the session context; read by ``_process``.
    _session: Session
    _album: Album

    def __init__(
        self,
        album_id: int,
        session_factory: SessionFactory,
        *,
        force: bool = False,
    ) -> None:
        self._album_id = album_id
        self._session_factory = session_factory
        self._force = force

    def run(self) -> ResultT:
        """Open the session, resolve + guard the album, run ``_process``, commit.

        Before committing, :meth:`_escalate_if_total_failure` turns a *systemic*
        failure (every attempted photo failed — Immich down, model missing) into a
        raised ``RuntimeError`` rather than a silently "done" stage; the raise leaves
        the session uncommitted (a fan-out stage's own ``new_session`` rolls back on
        exit), so nothing partial is persisted. Start/done INFO logging is hoisted
        here so every stage gets it for free.
        """
        _log.info("%s: starting (album_id=%s)", self.task_name, self._album_id)
        with self._session_factory() as session:
            self._session = session
            self._album = self._load_album(self._album_id, session)
            result = self._process()
            self._escalate_if_total_failure(result)
            session.flush()
            session.commit()
            _log.info("%s: done (album_id=%s) — %r", self.task_name, self._album_id, result)
            return result

    def _escalate_if_total_failure(self, result: ResultT) -> None:
        """Raise ``RuntimeError`` iff every *attempted* item failed.

        Reads :meth:`_failure_counts`; ``None`` (the default) means the stage has no
        per-item failure isolation and is never escalated. ``attempted`` excludes
        items short-circuited before any work (``skipped``/``unclustered``/
        ``not_ready``), so one bad photo among many never trips this — only a run
        where nothing that was actually attempted succeeded.
        """
        counts = self._failure_counts(result)
        if counts is None:
            return
        failed, attempted = counts
        if attempted > 0 and failed == attempted:
            raise StageTotalFailure(
                f"{self.task_name}: failed for all {attempted} attempted photo(s) — "
                f"treating as a systemic failure, not a completed stage ({result!r})",
            )

    def _failure_counts(self, result: ResultT) -> tuple[int, int] | None:
        """``(failed, attempted)`` for a stage with per-photo failure isolation, or
        ``None`` (the default) to opt out of total-failure escalation entirely.

        Overridden by the four isolating stages (``quality``/``enhance``/``render``/
        ``caption``); left at ``None`` for ``sync``/``cluster``/``select``/
        ``assemble``/``fulfill``, which have no per-photo ``failed`` count.
        """
        return None

    @staticmethod
    def _load_album(album_id: int, session: Session) -> Album:
        """Fetch the album, raising if ``album_id`` names no row.

        The one guard every stage shares — no stage assumes its ``album_id`` is
        valid (folder-structure.md structure rules).
        """
        album = session.get(Album, album_id)
        if album is None:
            raise ValueError(f"no album with id {album_id}")
        return album

    @abstractmethod
    def _process(self) -> ResultT:
        """Do the stage's actual work against ``self._album`` / ``self._session``."""
