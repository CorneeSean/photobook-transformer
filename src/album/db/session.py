"""Engine + session factory built from ``DATABASE_URL``.

The pure-logic stages are tested against an in-memory SQLite session (see
tests/conftest.py); this module is how the real pipeline (``flow.py``, step 14)
gets a session against our Postgres. Kept tiny and lazy so importing it never
requires ``DATABASE_URL`` to be set — only calling into it does.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from album.config import get_settings

# A zero-arg callable that opens a fresh, independent Session — e.g. so each of
# flow.py's fan-out tasks can own its own pooled connection instead of sharing
# one across threads (pipeline-execution.md §"The change"). `new_session`
# already satisfies this shape and is the default factory.
SessionFactory = Callable[[], Session]


@cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url)


@cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def new_session() -> Session:
    """A fresh :class:`Session` bound to our Postgres engine."""
    return _session_factory()()


__all__ = ["SessionFactory", "get_engine", "new_session"]
