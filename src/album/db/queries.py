"""Shared read queries over the ORM models.

Several stages open their own session and read the *same* slice of an album.
Centralizing those reads here keeps one definition per query, so a change to
*what* a slice means (its predicate or ordering) lands in a single place rather
than drifting across the stages that copy it (open question 7). Each function
takes the caller's :class:`~sqlalchemy.orm.Session` and never owns one, so it
composes with the per-task session model the parallel flow relies on
([pipeline-execution.md](/foundations/pipeline-execution.md)) — same posture as
[`similarity.py`](./similarity.py).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Photo


def selected_photos(album_id: int, session: Session) -> Sequence[Photo]:
    """The album's ``selected`` photos, ordered by ``id`` for deterministic
    iteration — the one read shared by ``caption``/``enhance``/``render``."""
    return list(
        session.scalars(
            select(Photo).where(Photo.album_id == album_id, Photo.selected).order_by(Photo.id),
        ),
    )


__all__ = ["selected_photos"]
