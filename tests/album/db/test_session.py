"""Tests for the session factories in :mod:`album.db.session`.

The pooled ``new_session`` factory needs a real ``DATABASE_URL`` and is exercised
end-to-end by the pgvector/flow integration suites; what's unit-testable here is
:func:`~album.db.session.reuse`, the *singleton* factory the four sequential stages
run on — its whole contract is "yield the shared session, and never close it."
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from album.db.models import Base
from album.db.session import reuse


class TestReuse:
    """`reuse(session)` yields that exact session without closing it on exit."""

    @pytest.fixture
    def session(self) -> Session:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        return Session(engine)

    def test_yields_the_same_session(self, session: Session) -> None:
        with reuse(session)() as reused:
            assert reused is session

    def test_does_not_close_on_exit(self, session: Session) -> None:
        """The Pipeline owns the shared session's lifecycle, so a stage reusing it
        via ``reuse`` must not close it — otherwise the next sequential stage (and
        the flow's own final ``close``) would operate on a closed session."""
        closed: list[bool] = []
        real_close: Callable[[], None] = session.close

        def _spy_close() -> None:
            closed.append(True)
            real_close()

        session.close = _spy_close  # pyright: ignore[reportAttributeAccessIssue]

        with reuse(session)() as reused:
            reused.execute(select(1))

        assert closed == []  # the context manager did NOT close the session
        # Still usable after the block — the point of not closing it.
        assert session.execute(select(1)).scalar_one() == 1
        real_close()
