"""Engine + session factory built from ``DATABASE_URL``.

The pure-logic stages are tested against an in-memory SQLite session (see
tests/conftest.py); this module is how the real pipeline (``flow.py``, step 14)
gets a session against our Postgres. Kept tiny and lazy so importing it never
requires ``DATABASE_URL`` to be set — only calling into it does.
"""

from __future__ import annotations

from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from album.config import get_settings


@cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url)


@cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def new_session() -> Session:
    """A fresh :class:`Session` bound to our Postgres engine."""
    return _session_factory()()
