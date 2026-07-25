"""The ``Stage`` Template Method base (quality-practices.md §1.6.1).

Exercised through a minimal concrete subclass — the base's contract is: the
constructor loads and guards the album (storing it as ``self._album``) and records
``force``, and the no-argument ``run`` delegates to ``_process`` (which reads those
stored fields), then flushes + commits. The real stages inherit exactly this.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

import pytest
from sqlalchemy.orm import Session

from album.db.models import Album
from album.db.session import SessionFactory, reuse
from album.flow.stages.base import Stage, StageTotalFailure


@dataclass
class _Captured:
    """What the spy stage saw when the base delegated to ``_process``."""

    album: Album
    force: bool


class _SpyStage(Stage[_Captured]):
    """Echoes back the stored ``self._album`` / ``self._force`` from ``_process``."""

    task_name: ClassVar[str] = "spy"

    def _process(self) -> _Captured:
        return _Captured(album=self._album, force=self._force)


@dataclass
class _Counted:
    """A per-photo stage's result: how many were attempted vs. failed."""

    attempted: int = 0
    failed: int = 0


class _CountingStage(Stage[_Counted]):
    """A stage with per-photo failure isolation, so it reports ``_failure_counts``.

    ``_process`` returns whatever counts the test seeded, so the base's total-failure
    escalation can be exercised without a real per-photo loop.
    """

    task_name: ClassVar[str] = "counting"

    def __init__(self, result: _Counted, album_id: int, session_factory: SessionFactory) -> None:
        super().__init__(album_id, session_factory)
        self._result = result

    def _process(self) -> _Counted:
        return self._result

    def _failure_counts(self, result: _Counted) -> tuple[int, int]:
        return result.failed, result.attempted


class TestStageTemplateMethod:
    """The constructor resolves the album via the invariant guard; ``run`` delegates."""

    def test_delegates_resolved_album_to_process(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album = make_album()
        session.add(album)
        session.flush()

        captured = _SpyStage(album.id, reuse(session)).run()

        assert captured.album is album

    def test_force_is_threaded_through_to_process(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album = make_album()
        session.add(album)
        session.flush()

        assert _SpyStage(album.id, reuse(session), force=True).run().force is True
        assert _SpyStage(album.id, reuse(session)).run().force is False

    def test_missing_album_raises_in_run(self, session: Session) -> None:
        with pytest.raises(ValueError, match="no album with id 999"):
            _SpyStage(999, reuse(session)).run()


class TestTotalFailureEscalation:
    """``run`` raises when every *attempted* item failed — a systemic failure (Immich
    down, model missing) must escalate, not report a completed stage. ``_failure_counts``
    returning ``None`` (the default) opts a stage out entirely."""

    def _persist(self, session: Session, make_album: Callable[..., Album]) -> int:
        album = make_album()
        session.add(album)
        session.flush()
        return album.id

    def test_raises_when_all_attempted_items_failed(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album_id = self._persist(session, make_album)
        stage = _CountingStage(_Counted(attempted=3, failed=3), album_id, reuse(session))

        with pytest.raises(StageTotalFailure, match="failed for all 3 attempted"):
            stage.run()

    def test_does_not_raise_on_partial_failure(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        album_id = self._persist(session, make_album)
        stage = _CountingStage(_Counted(attempted=3, failed=2), album_id, reuse(session))

        assert stage.run() == _Counted(attempted=3, failed=2)

    def test_does_not_raise_when_nothing_was_attempted(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        # An album where every photo was short-circuited (skipped/unclustered/not_ready)
        # is attempted=0 — not a systemic failure, so no escalation.
        album_id = self._persist(session, make_album)
        stage = _CountingStage(_Counted(attempted=0, failed=0), album_id, reuse(session))

        assert stage.run() == _Counted()

    def test_default_hook_never_escalates(
        self,
        session: Session,
        make_album: Callable[..., Album],
    ) -> None:
        # _SpyStage doesn't override _failure_counts -> the base returns None -> a stage
        # like sync/cluster/select is never subject to escalation.
        album_id = self._persist(session, make_album)
        assert _SpyStage(album_id, reuse(session)).run().force is False


class TestStageLogging:
    """``run`` emits a start line and a done line (carrying the result ``repr``) for
    every stage — hoisted onto the base so it's free for all of them."""

    def test_logs_start_and_done_with_result_repr(
        self,
        session: Session,
        make_album: Callable[..., Album],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        album = make_album()
        session.add(album)
        session.flush()

        with caplog.at_level(logging.INFO, logger="album.flow.stages.base"):
            _CountingStage(_Counted(attempted=2, failed=1), album.id, reuse(session)).run()

        messages = [r.message for r in caplog.records]
        assert any("counting" in m and "starting" in m for m in messages)
        assert any("_Counted(attempted=2, failed=1)" in m for m in messages)
