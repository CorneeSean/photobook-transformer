"""Engine + session factory built from ``DATABASE_URL``.

The pure-logic stages are tested against an in-memory SQLite session (see
tests/conftest.py); this module is how the real pipeline (``flow.py``, step 14)
gets a session against our Postgres. Kept tiny and lazy so importing it never
requires ``DATABASE_URL`` to be set — only calling into it does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from album.config import get_settings

# A zero-arg callable that opens a Session *as a context manager* — a stage calls
# it inside ``run`` (``with self._session_factory() as session``) so the session is
# created on whatever thread runs the stage. `new_session` returns a fresh, pooled
# Session (a context manager that closes it on exit) — the factory a fan-out stage
# uses to own its own connection per worker thread (pipeline-execution.md §"The
# change"). :func:`reuse` is the other implementation, for the shared session.
SessionFactory = Callable[[], AbstractContextManager[Session]]


@cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url)


@cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def new_session() -> Session:
    """A fresh :class:`Session` bound to our Postgres engine.

    A ``Session`` is itself a context manager (closing on exit), so ``new_session``
    already satisfies :data:`SessionFactory` — it's the default fan-out factory.
    """
    return _session_factory()()


def reuse(session: Session) -> SessionFactory:
    """A *singleton* :data:`SessionFactory`: every call yields the same,
    already-open ``session`` and does **not** close it on exit.

    This is how the four *sequential* stages run on the flow's **shared** session:
    the ``Pipeline`` owns that session's lifecycle, so a stage reusing it must not
    close it (contrast :func:`new_session`, whose context closes the session it
    opened). The stage still flushes + commits it in ``run`` — the shared session
    is committed after each sequential stage, exactly as before.
    """

    @contextmanager
    def factory() -> Iterator[Session]:
        yield session

    return factory


__all__ = ["SessionFactory", "get_engine", "new_session", "reuse"]
