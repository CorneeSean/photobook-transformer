"""Transaction-boundary wrappers shared by the per-stage ``@task`` modules.

Since this refactor each stage module (``flow/stages/*.py``) defines its **own**
Prefect ``@task`` (co-located with the stage it wraps — see folder-structure.md
and quality-practices.md §1.5). The session/commit *policy* those tasks run under
is, however, still an **orchestration** concern, not a per-stage one — so it lives
here, in one shared module, rather than being restated in every stage module. Two
wrappers, matching the two transaction boundaries the DAG needs
(pipeline-execution.md §"The change"):

- :func:`autocommit` — the task runs on the flow's **shared** ``Session`` (opened
  and closed by :class:`~album.flow._flow.Pipeline`), committing on success. Used
  by the four *sequential* stages (``sync``, ``select``, ``assemble``,
  ``fulfill``).
- :func:`owns_session` — the task opens its **own** pooled ``Session`` from a
  ``session_factory``, committing on success. Used by the six *fan-out* stages,
  which run on real threads under Prefect's ``ThreadPoolTaskRunner`` where a shared
  ``Session`` isn't safe.

Both wrap a task whose **first argument is the constructed stage instance**
(quality-practices.md §1.5): the stage's dependency injection stays in the
:class:`~album.flow._flow.Pipeline` wiring layer, and the task itself is a thin
``stage.run(album_id, session)`` call under the right transaction boundary.

Neither uses ``functools.wraps``: copying the inner function's signature would make
Prefect's ``.submit(...)`` parameter binding target the inner ``session``
parameter instead of this wrapper's own ``session`` / ``session_factory``
parameter. The explicit outward signature here (and no ``@wraps``) is what makes
``.submit()`` bind correctly; each stage module passes ``name=`` to its ``@task``
for a readable run name, since ``wrapper.__name__`` is now generic.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from album.db.session import SessionFactory


def autocommit(fn: Callable[..., None]) -> Callable[..., None]:
    """Wrap a stage task so it runs on the flow's shared ``session`` and commits.

    For the four *sequential* tasks, which take the shared ``Session`` directly
    (opened/closed by :class:`~album.flow._flow.Pipeline`, not by the task). The
    outward signature matches the inner one in shape — ``(stage, album_id,
    session)`` — so there is no Prefect-introspection hazard; ``@wraps`` is omitted
    only for the generic-``__name__`` reason the module docstring gives.
    """

    def wrapper(
        stage: object, album_id: int, session: Session, /, *args: object, **kwargs: object
    ) -> None:
        fn(stage, album_id, session, *args, **kwargs)
        session.commit()

    return wrapper


def owns_session(fn: Callable[..., None]) -> Callable[..., None]:
    """Wrap a stage task so it runs on its **own** pooled session and commits.

    For the six *fan-out* tasks, which must not share the flow's ``Session`` across
    threads (pipeline-execution.md §"The change" §1). The outward signature takes a
    ``session_factory`` where the inner function takes a ``session``; the wrapper
    opens the session, runs the stage, and commits on success. Its own
    ``with``-block rolls back independently on failure, so one failed fan-out task
    doesn't poison the others.
    """

    def wrapper(
        stage: object,
        album_id: int,
        session_factory: SessionFactory,
        /,
        *args: object,
        **kwargs: object,
    ) -> None:
        with session_factory() as session:
            fn(stage, album_id, session, *args, **kwargs)
            session.commit()

    return wrapper
