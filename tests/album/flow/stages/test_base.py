"""The ``Stage`` Template Method base (quality-practices.md §1.6.1).

Exercised through a minimal concrete subclass — the base's contract is: the
constructor loads and guards the album (storing it as ``self._album``) and records
``force``, and the no-argument ``run`` delegates to ``_process`` (which reads those
stored fields), then flushes + commits. The real stages inherit exactly this.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from album.db.models import Album
from album.db.session import reuse
from album.flow.stages.base import Stage


@dataclass
class _Captured:
    """What the spy stage saw when the base delegated to ``_process``."""

    album: Album
    force: bool


class _SpyStage(Stage[_Captured]):
    """Echoes back the stored ``self._album`` / ``self._force`` from ``_process``."""

    def _process(self) -> _Captured:
        return _Captured(album=self._album, force=self._force)


class TestStageTemplateMethod:
    """The constructor resolves the album via the invariant guard; ``run`` delegates."""

    def test_delegates_resolved_album_to_process(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album = make_album()
        session.add(album)
        session.flush()

        captured = _SpyStage(album.id, reuse(session)).run()

        assert captured.album is album

    def test_force_is_threaded_through_to_process(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album = make_album()
        session.add(album)
        session.flush()

        assert _SpyStage(album.id, reuse(session), force=True).run().force is True
        assert _SpyStage(album.id, reuse(session)).run().force is False

    def test_missing_album_raises_in_run(self, session: Session) -> None:
        with pytest.raises(ValueError, match="no album with id 999"):
            _SpyStage(999, reuse(session)).run()
