"""The ``Stage`` Template Method base (quality-practices.md §1.6.1).

Exercised through a minimal concrete subclass — the base's contract is: ``run``
loads and guards the album, then delegates to ``_process`` with the resolved
``Album`` and the forwarded ``force`` flag. The real stages inherit exactly this.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from album.db.models import Album
from album.flow.stages.base import Stage


@dataclass
class _Captured:
    """What the spy stage saw when the base delegated to ``_process``."""

    album: Album
    force: bool


class _SpyStage(Stage[_Captured]):
    """Records the arguments the base hands to ``_process`` and echoes them back."""

    def _process(self, album: Album, session: Session, *, force: bool) -> _Captured:
        return _Captured(album=album, force=force)


class TestStageTemplateMethod:
    """`run` resolves the album via the invariant guard, then delegates."""

    def test_delegates_resolved_album_to_process(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album = make_album()
        session.add(album)
        session.flush()

        captured = _SpyStage().run(album.id, session)

        assert captured.album is album

    def test_force_is_threaded_through_to_process(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album = make_album()
        session.add(album)
        session.flush()

        assert _SpyStage().run(album.id, session, force=True).force is True
        assert _SpyStage().run(album.id, session).force is False

    def test_missing_album_raises_before_process(self, session: Session) -> None:
        with pytest.raises(ValueError, match="no album with id 999"):
            _SpyStage().run(999, session)
