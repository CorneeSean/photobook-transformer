"""``select`` — diversity-aware best-of-scene via MMR, scoped by ``album_id``.

Within each [scene](/stages/cluster.md), greedily walk candidates by
``aesthetic_score`` (highest first) and pick a candidate unless:

- it's an Immich-flagged duplicate (``duplicate_group_id``) of an already-picked
  photo in the same scene, or
- its CLIP cosine similarity to any already-picked photo in the scene exceeds
  ``threshold`` (near-duplicate).

``clip_embedding`` is nullable (data-model.md) — a candidate or already-picked
photo without one simply skips the CLIP check rather than crashing the stage;
the ``duplicate_group_id`` check still applies.

Photos with no ``scene_id`` (unclustered — see [`cluster`](./cluster.py)) are
never selected. Pure logic over data already in the DB — the ``pgvector``
similarity query sits behind the substitutable :class:`~album.db.similarity.SimilaritySearch`
interface, so this module is unit-testable on in-memory SQLite with similarity
stubbed (see [build step 8](/build-order.md)).

Idempotent: resets ``selected`` for the album up front, so re-running recomputes
from scratch rather than compounding. No Prefect imports (folder-structure.md
rule). Every stage takes ``album_id`` first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.db.similarity import SimilaritySearch

_log = logging.getLogger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.9


@dataclass
class SelectResult:
    selected: int = 0
    scenes: int = 0


def select(
    album_id: int,
    session: Session,
    similarity: SimilaritySearch,
    *,
    threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
) -> SelectResult:
    """Pick the best, diverse photos per scene in ``album_id``. Idempotent."""
    if session.get(Album, album_id) is None:
        raise ValueError(f"no album with id {album_id}")

    # order_by(Photo.id): deterministic tie-breaking when aesthetic scores are equal.
    photos = list(
        session.scalars(sa_select(Photo).where(Photo.album_id == album_id).order_by(Photo.id))
    )
    for photo in photos:  # reset so a re-run is deterministic
        photo.selected = False

    by_scene: dict[int, list[Photo]] = {}
    for photo in photos:
        if photo.scene_id is None:  # unclustered — never selected
            continue
        by_scene.setdefault(photo.scene_id, []).append(photo)

    result = SelectResult(scenes=len(by_scene))
    for scene_id in sorted(by_scene):
        result.selected += _select_scene(by_scene[scene_id], session, similarity, threshold)

    session.flush()
    return result


def _select_scene(
    candidates: list[Photo], session: Session, similarity: SimilaritySearch, threshold: float
) -> int:
    """Greedy MMR over one scene's candidates. Returns the number picked."""

    def score(p: Photo) -> float:
        return p.aesthetic_score if p.aesthetic_score is not None else float("-inf")

    ranked = sorted(candidates, key=score, reverse=True)
    picked: list[Photo] = []
    picked_dup_groups: set[str] = set()
    for candidate in ranked:
        already_picked_dup = (
            candidate.duplicate_group_id is not None
            and candidate.duplicate_group_id in picked_dup_groups
        )
        if already_picked_dup:
            continue
        if picked:
            max_sim = similarity.max_similarity(
                session, candidate.clip_embedding, [p.id for p in picked]
            )
            if max_sim > threshold:
                continue
        candidate.selected = True
        picked.append(candidate)
        if candidate.duplicate_group_id is not None:
            picked_dup_groups.add(candidate.duplicate_group_id)
    return len(picked)
