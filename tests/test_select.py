"""Tests for the ``select`` stage over in-memory SQLite — MMR selection logic.

Thorough by design (build-order step 8): highest-aesthetic-score-per-scene wins,
near-duplicates (via a stubbed similarity search) are skipped, Immich-flagged
duplicates are skipped, an all-similar scene yields exactly one selection, a
diverse scene yields several, unclustered photos are never selected, and a
missing ``clip_embedding`` (nullable — data-model.md) never crashes the stage.
Similarity is a substitutable stub here; the real ``pgvector`` query is covered
separately in test_similarity.py against a Postgres test container.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.stages.select import SelectResult, select

from .conftest import make_album, make_photo

# Unit-ish vectors chosen so cosine similarity is easy to reason about:
_A = [1.0, 0.0, 0.0]
_A_NEAR = [0.99, 0.14, 0.0]  # cosine sim to _A ~0.99 — a near-duplicate
_B = [0.0, 1.0, 0.0]  # orthogonal to _A — cosine sim 0.0
_C = [0.0, 0.0, 1.0]  # orthogonal to both — cosine sim 0.0


class StubSimilarity:
    """Cosine similarity computed in plain numpy. Mirrors the real query's shape
    (session + already-picked ids): SQLite can store and read back
    ``clip_embedding`` values even though it can't run pgvector's
    ``cosine_distance`` (data-model.md), so this reads embeddings straight off
    the ORM objects rather than keeping a separate registry."""

    def max_similarity(
        self, session: Session, embedding: list[float] | None, against: list[int]
    ) -> float:
        if embedding is None or not against:
            return -1.0
        a = np.array(embedding)
        others = session.scalars(sa_select(Photo).where(Photo.id.in_(against))).all()
        sims = [
            float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
            for b in (np.array(p.clip_embedding) for p in others if p.clip_embedding is not None)
        ]
        return max(sims) if sims else -1.0


def _persist(session: Session, album: Album) -> int:
    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


def _photo(
    album: Album,
    asset: str,
    *,
    scene_id: int | None,
    aesthetic_score: float | None,
    duplicate_group_id: str | None = None,
    clip_embedding: list[float] | None = None,
) -> Photo:
    return make_photo(
        album,
        immich_asset_id=asset,
        filename=f"{asset}.jpg",
        scene_id=scene_id,
        aesthetic_score=aesthetic_score,
        duplicate_group_id=duplicate_group_id,
        clip_embedding=clip_embedding,
    )


def _by_asset(session: Session) -> dict[str, Photo]:
    return {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}


def test_highest_aesthetic_score_wins_first(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    low = _photo(album, "low", scene_id=1, aesthetic_score=0.2, clip_embedding=_A)
    # dissimilar, so both could be picked; high goes first
    high = _photo(album, "high", scene_id=1, aesthetic_score=0.9, clip_embedding=_B)
    album.photos = [low, high]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity())

    photos = _by_asset(session)
    assert photos["high"].selected is True
    assert result.selected == 2  # dissimilar, so both survive
    assert result.scenes == 1


def test_near_duplicates_skipped(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    best = _photo(album, "best", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
    # ~0.99 cosine sim to best, above threshold
    near_dup = _photo(album, "near_dup", scene_id=1, aesthetic_score=0.8, clip_embedding=_A_NEAR)
    album.photos = [best, near_dup]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity(), threshold=0.9)

    photos = _by_asset(session)
    assert photos["best"].selected is True
    assert photos["near_dup"].selected is False
    assert result.selected == 1


def test_immich_flagged_duplicates_skipped(session: Session) -> None:
    # Visually dissimilar (would pass the CLIP check) but Immich itself flagged
    # them as duplicates — that signal must skip on its own.
    album = make_album(immich_album_id="immich-abc")
    best = _photo(
        album,
        "best",
        scene_id=1,
        aesthetic_score=0.9,
        duplicate_group_id="dup-1",
        clip_embedding=_A,
    )
    # orthogonal — CLIP alone wouldn't skip this
    worse = _photo(
        album,
        "worse",
        scene_id=1,
        aesthetic_score=0.5,
        duplicate_group_id="dup-1",
        clip_embedding=_B,
    )
    album.photos = [best, worse]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity())

    photos = _by_asset(session)
    assert photos["best"].selected is True
    assert photos["worse"].selected is False
    assert result.selected == 1


def test_all_similar_scene_yields_exactly_one_selection(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    # identical embeddings — maximally similar
    photos = [
        _photo(album, f"p{i}", scene_id=1, aesthetic_score=score, clip_embedding=_A)
        for i, score in enumerate([0.9, 0.7, 0.5, 0.3])
    ]
    album.photos = photos
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity())

    assert result == SelectResult(selected=1, scenes=1)
    selected = [p for p in session.scalars(sa_select(Photo)) if p.selected]
    assert len(selected) == 1
    assert selected[0].immich_asset_id == "p0"  # the top-scoring one


def test_diverse_scene_yields_multiple_selections(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    p_a = _photo(album, "a", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
    p_b = _photo(album, "b", scene_id=1, aesthetic_score=0.8, clip_embedding=_B)
    p_c = _photo(album, "c", scene_id=1, aesthetic_score=0.7, clip_embedding=_C)
    album.photos = [p_a, p_b, p_c]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity())

    assert result == SelectResult(selected=3, scenes=1)
    assert all(p.selected for p in session.scalars(sa_select(Photo)))


def test_unclustered_photos_never_selected(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    clustered = _photo(album, "clustered", scene_id=1, aesthetic_score=0.5, clip_embedding=_A)
    unclustered = _photo(
        album, "unclustered", scene_id=None, aesthetic_score=0.9, clip_embedding=_B
    )
    album.photos = [clustered, unclustered]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity())

    photos = _by_asset(session)
    assert photos["clustered"].selected is True
    assert photos["unclustered"].selected is False
    # unclustered photo doesn't count as a scene
    assert result == SelectResult(selected=1, scenes=1)


def test_multiple_scenes_selected_independently(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    s1_best = _photo(album, "s1_best", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
    # same scene, identical embedding as s1_best
    s1_worst = _photo(album, "s1_worst", scene_id=1, aesthetic_score=0.1, clip_embedding=_A)
    # different scene, so similarity to scene 1 is irrelevant
    s2_best = _photo(album, "s2_best", scene_id=2, aesthetic_score=0.8, clip_embedding=_A)
    album.photos = [s1_best, s1_worst, s2_best]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity())

    photos = _by_asset(session)
    assert photos["s1_best"].selected is True
    assert photos["s1_worst"].selected is False
    assert photos["s2_best"].selected is True
    assert result == SelectResult(selected=2, scenes=2)


def test_photo_without_embedding_is_not_dropped(session: Session) -> None:
    # clip_embedding is nullable (data-model.md) — a candidate select can't
    # score similarity for must not crash the stage or get silently excluded.
    album = make_album(immich_album_id="immich-abc")
    first = _photo(album, "first", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
    no_embedding = _photo(
        album, "no_embedding", scene_id=1, aesthetic_score=0.8, clip_embedding=None
    )
    album.photos = [first, no_embedding]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity())

    photos = _by_asset(session)
    assert photos["first"].selected is True
    assert photos["no_embedding"].selected is True  # can't be judged similar, so kept
    assert result.selected == 2


def test_already_picked_without_embedding_does_not_crash(session: Session) -> None:
    # An already-picked photo lacking an embedding must not crash later
    # candidates' similarity checks; they compare only against picks that do
    # have one.
    album = make_album(immich_album_id="immich-abc")
    picked_no_embedding = _photo(
        album, "picked_no_embedding", scene_id=1, aesthetic_score=0.9, clip_embedding=None
    )
    near_dup_of_a = _photo(
        album, "near_dup_of_a", scene_id=1, aesthetic_score=0.7, clip_embedding=_A_NEAR
    )
    also_a = _photo(album, "also_a", scene_id=1, aesthetic_score=0.5, clip_embedding=_A)
    album.photos = [picked_no_embedding, near_dup_of_a, also_a]
    album_id = _persist(session, album)

    result = select(album_id, session, StubSimilarity(), threshold=0.9)

    photos = _by_asset(session)
    assert photos["picked_no_embedding"].selected is True  # top score, always picked first
    assert photos["near_dup_of_a"].selected is True  # can't compare to the embedding-less pick
    assert photos["also_a"].selected is False  # near-duplicate of near_dup_of_a (both ~_A)
    assert result.selected == 2


def test_is_idempotent(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    p_a = _photo(album, "a", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
    p_b = _photo(album, "b", scene_id=1, aesthetic_score=0.8, clip_embedding=_B)
    album.photos = [p_a, p_b]
    album_id = _persist(session, album)
    sim = StubSimilarity()

    first = select(album_id, session, sim)
    before = {a: p.selected for a, p in _by_asset(session).items()}
    second = select(album_id, session, sim)
    after = {a: p.selected for a, p in _by_asset(session).items()}

    assert first == second == SelectResult(selected=2, scenes=1)
    assert before == after


def test_scoped_to_album(session: Session) -> None:
    other = make_album(slug="other", immich_album_id="immich-other")
    other_photo = _photo(other, "o1", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
    other.photos = [other_photo]
    other_id = _persist(session, other)
    target = make_album(slug="target", immich_album_id="immich-target")
    target_photo = _photo(target, "t1", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
    target.photos = [target_photo]
    target_id = _persist(session, target)

    select(target_id, session, StubSimilarity())

    other_row = session.scalars(sa_select(Photo).where(Photo.album_id == other_id)).one()
    assert other_row.selected is False  # untouched
    target_row = session.scalars(sa_select(Photo).where(Photo.album_id == target_id)).one()
    assert target_row.selected is True


def test_raises_for_unknown_album(session: Session) -> None:
    with pytest.raises(ValueError, match="no album"):
        select(999, session, StubSimilarity())
