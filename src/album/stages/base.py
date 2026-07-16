"""The ``Stage`` Template Method base shared by every pipeline stage.

Every stage's ``run`` opens with the same invariant envelope — load the album by
``album_id`` and guard that it exists — before doing anything stage-specific. That
skeleton is a **Template Method** (quality-practices.md §1.6.1): this base owns it
so no stage restates it, and delegates the variant work to the abstract
``_process`` hook.

Only the genuinely invariant step is hoisted. The *idempotency* short-circuit is
deliberately **not** here — it varies structurally across stages (an album-level
early-return for ``assemble``/``fulfill``, a per-photo skip for
``quality``/``enhance``/``caption``/``render``, none for
``sync``/``cluster``/``select``) — so each ``_process`` keeps its own.

No Prefect import lives here (folder-structure.md): ``flow.py`` wraps the
inherited ``.run`` as a ``@task``. ``Stage`` is generic over its result type, so a
subclass keeps its own concrete ``@dataclass`` return —
``class SyncStage(Stage[SyncResult])`` with ``_process(...) -> SyncResult``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from album.db.models import Album


class Stage[ResultT](ABC):
    """Template Method base for a pipeline stage.

    ``run`` is the invariant algorithm (load + guard the album, then delegate);
    ``_process`` is the per-stage step subclasses implement. ``album_id`` comes
    first and the ``Session`` is request-scoped — never a constructor dependency
    (quality-practices.md §1.2). ``force`` is keyword-only and is threaded through
    to ``_process``; a stage with no per-run idempotency simply ignores it.
    """

    def run(self, album_id: int, session: Session, *, force: bool = False) -> ResultT:
        """Load and guard ``album_id``, then run the stage-specific ``_process``."""
        album = self._load_album(album_id, session)
        return self._process(album, session, force=force)

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
    def _process(self, album: Album, session: Session, *, force: bool) -> ResultT:
        """Do the stage's actual work against an already-loaded ``album``."""
