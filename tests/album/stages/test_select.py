"""Tests for :class:`~album.stages.select.SelectStage` over in-memory SQLite —
MMR selection logic.

Thorough by design (build-order step 8): highest-aesthetic-score-per-scene wins,
near-duplicates (via a stubbed similarity search) are skipped, Immich-flagged
duplicates are skipped, an all-similar scene yields exactly one selection, a
diverse scene yields several, unclustered photos are never selected, and a
missing ``clip_embedding`` (nullable — data-model.md) never crashes the stage.
Similarity is a substitutable stub here; the real ``pgvector`` query is covered
separately in test_similarity.py against a Postgres test container.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Person, Photo
from album.stages.select import SelectResult, SelectStage

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


class TestSelect:
    """`SelectStage.run`: MMR selection, duplicate handling, and idempotency."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def _by_asset(self, session: Session) -> dict[str, Photo]:
        return {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}

    @pytest.fixture
    def photo(self, make_photo: Callable[..., Photo]) -> Callable[..., Photo]:
        """Builder for a select-test photo: scene, aesthetic score, embedding."""

        def _make(
            album: Album,
            asset: str,
            *,
            scene_id: int | None,
            aesthetic_score: float | None,
            duplicate_group_id: str | None = None,
            clip_embedding: list[float] | None = None,
            person_ids: list[str] | None = None,
        ) -> Photo:
            return make_photo(
                album,
                immich_asset_id=asset,
                filename=f"{asset}.jpg",
                scene_id=scene_id,
                aesthetic_score=aesthetic_score,
                duplicate_group_id=duplicate_group_id,
                clip_embedding=clip_embedding,
                person_ids=person_ids,
            )

        return _make

    def test_highest_aesthetic_score_wins_first(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        low = photo(album, "low", scene_id=1, aesthetic_score=0.2, clip_embedding=_A)
        # dissimilar, so both could be picked; high goes first
        high = photo(album, "high", scene_id=1, aesthetic_score=0.9, clip_embedding=_B)
        album.photos = [low, high]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity()).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["high"].selected is True
        assert result.selected == 2  # dissimilar, so both survive
        assert result.scenes == 1

    def test_near_duplicates_skipped(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        best = photo(album, "best", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
        # ~0.99 cosine sim to best, above threshold
        near_dup = photo(album, "near_dup", scene_id=1, aesthetic_score=0.8, clip_embedding=_A_NEAR)
        album.photos = [best, near_dup]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity(), threshold=0.9).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["best"].selected is True
        assert photos["near_dup"].selected is False
        assert result.selected == 1

    def test_immich_flagged_duplicates_skipped(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # Visually dissimilar (would pass the CLIP check) but Immich itself flagged
        # them as duplicates — that signal must skip on its own.
        album = make_album(immich_album_id="immich-abc")
        best = photo(
            album,
            "best",
            scene_id=1,
            aesthetic_score=0.9,
            duplicate_group_id="dup-1",
            clip_embedding=_A,
        )
        # orthogonal — CLIP alone wouldn't skip this
        worse = photo(
            album,
            "worse",
            scene_id=1,
            aesthetic_score=0.5,
            duplicate_group_id="dup-1",
            clip_embedding=_B,
        )
        album.photos = [best, worse]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity()).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["best"].selected is True
        assert photos["worse"].selected is False
        assert result.selected == 1

    def test_all_similar_scene_yields_exactly_one_selection(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        # identical embeddings — maximally similar
        album.photos = [
            photo(album, f"p{i}", scene_id=1, aesthetic_score=score, clip_embedding=_A)
            for i, score in enumerate([0.9, 0.7, 0.5, 0.3])
        ]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity()).run(album_id, session)

        assert result == SelectResult(selected=1, scenes=1)
        selected = [p for p in session.scalars(sa_select(Photo)) if p.selected]
        assert len(selected) == 1
        assert selected[0].immich_asset_id == "p0"  # the top-scoring one

    def test_diverse_scene_yields_multiple_selections(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        album.photos = [
            photo(album, "a", scene_id=1, aesthetic_score=0.9, clip_embedding=_A),
            photo(album, "b", scene_id=1, aesthetic_score=0.8, clip_embedding=_B),
            photo(album, "c", scene_id=1, aesthetic_score=0.7, clip_embedding=_C),
        ]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity()).run(album_id, session)

        assert result == SelectResult(selected=3, scenes=1)
        assert all(p.selected for p in session.scalars(sa_select(Photo)))

    def test_unclustered_photos_never_selected(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        clustered = photo(album, "clustered", scene_id=1, aesthetic_score=0.5, clip_embedding=_A)
        unclustered = photo(
            album, "unclustered", scene_id=None, aesthetic_score=0.9, clip_embedding=_B
        )
        album.photos = [clustered, unclustered]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity()).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["clustered"].selected is True
        assert photos["unclustered"].selected is False
        # unclustered photo doesn't count as a scene
        assert result == SelectResult(selected=1, scenes=1)

    def test_multiple_scenes_selected_independently(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        s1_best = photo(album, "s1_best", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
        # same scene, identical embedding as s1_best
        s1_worst = photo(album, "s1_worst", scene_id=1, aesthetic_score=0.1, clip_embedding=_A)
        # different scene, so similarity to scene 1 is irrelevant
        s2_best = photo(album, "s2_best", scene_id=2, aesthetic_score=0.8, clip_embedding=_A)
        album.photos = [s1_best, s1_worst, s2_best]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity()).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["s1_best"].selected is True
        assert photos["s1_worst"].selected is False
        assert photos["s2_best"].selected is True
        assert result == SelectResult(selected=2, scenes=2)

    def test_photo_without_embedding_is_not_dropped(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # clip_embedding is nullable (data-model.md) — a candidate select can't
        # score similarity for must not crash the stage or get silently excluded.
        album = make_album(immich_album_id="immich-abc")
        first = photo(album, "first", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)
        no_embedding = photo(
            album, "no_embedding", scene_id=1, aesthetic_score=0.8, clip_embedding=None
        )
        album.photos = [first, no_embedding]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity()).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["first"].selected is True
        assert photos["no_embedding"].selected is True  # can't be judged similar, so kept
        assert result.selected == 2

    def test_already_picked_without_embedding_does_not_crash(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # An already-picked photo lacking an embedding must not crash later
        # candidates' similarity checks; they compare only against picks that do
        # have one.
        album = make_album(immich_album_id="immich-abc")
        picked_no_embedding = photo(
            album, "picked_no_embedding", scene_id=1, aesthetic_score=0.9, clip_embedding=None
        )
        near_dup_of_a = photo(
            album, "near_dup_of_a", scene_id=1, aesthetic_score=0.7, clip_embedding=_A_NEAR
        )
        also_a = photo(album, "also_a", scene_id=1, aesthetic_score=0.5, clip_embedding=_A)
        album.photos = [picked_no_embedding, near_dup_of_a, also_a]
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity(), threshold=0.9).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["picked_no_embedding"].selected is True  # top score, always picked first
        assert photos["near_dup_of_a"].selected is True  # can't compare to the embedding-less pick
        assert photos["also_a"].selected is False  # near-duplicate of near_dup_of_a (both ~_A)
        assert result.selected == 2

    def test_is_idempotent(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        album.photos = [
            photo(album, "a", scene_id=1, aesthetic_score=0.9, clip_embedding=_A),
            photo(album, "b", scene_id=1, aesthetic_score=0.8, clip_embedding=_B),
        ]
        album_id = self._persist(session, album)
        stage = SelectStage(StubSimilarity())

        first = stage.run(album_id, session)
        before = {a: p.selected for a, p in self._by_asset(session).items()}
        second = stage.run(album_id, session)
        after = {a: p.selected for a, p in self._by_asset(session).items()}

        assert first == second == SelectResult(selected=2, scenes=1)
        assert before == after

    def test_scoped_to_album(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        other = make_album(slug="other", immich_album_id="immich-other")
        other.photos = [photo(other, "o1", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)]
        other_id = self._persist(session, other)
        target = make_album(slug="target", immich_album_id="immich-target")
        target.photos = [photo(target, "t1", scene_id=1, aesthetic_score=0.9, clip_embedding=_A)]
        target_id = self._persist(session, target)

        SelectStage(StubSimilarity()).run(target_id, session)

        other_row = session.scalars(sa_select(Photo).where(Photo.album_id == other_id)).one()
        assert other_row.selected is False  # untouched
        target_row = session.scalars(sa_select(Photo).where(Photo.album_id == target_id)).one()
        assert target_row.selected is True

    def test_raises_for_unknown_album(self, session: Session) -> None:
        with pytest.raises(ValueError, match="no album"):
            SelectStage(StubSimilarity()).run(999, session)


class TestPeopleAwareSelect:
    """`select` step 18: people/lead-aware ranking, coverage, and the genericity guard.

    Faces (``Photo.person_ids``) and leads (``Person.is_lead``) add opt-in
    behaviors that must all reduce to the plain MMR result when a scene has no
    recognized faces. Similarity is the same numpy :class:`StubSimilarity`; the
    vectors ``_A``/``_A_NEAR`` are near-duplicates, ``_B`` is dissimilar.
    """

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def _by_asset(self, session: Session) -> dict[str, Photo]:
        return {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}

    @pytest.fixture
    def photo(self, make_photo: Callable[..., Photo]) -> Callable[..., Photo]:
        def _make(
            album: Album,
            asset: str,
            *,
            scene_id: int = 1,
            aesthetic_score: float | None,
            clip_embedding: list[float] | None = None,
            person_ids: list[str] | None = None,
        ) -> Photo:
            return make_photo(
                album,
                immich_asset_id=asset,
                filename=f"{asset}.jpg",
                scene_id=scene_id,
                aesthetic_score=aesthetic_score,
                clip_embedding=clip_embedding,
                person_ids=person_ids,
            )

        return _make

    @staticmethod
    def _lead(album: Album, *person_ids: str) -> None:
        for person_id in person_ids:
            album.people.append(Person(immich_person_id=person_id, is_lead=True))

    def test_group_shot_wins_tiebreak_when_scores_close(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # All-similar scene ⇒ exactly one pick; equal aesthetic ⇒ the group shot
        # (more recognized people) edges out the incidental solo.
        album = make_album(immich_album_id="immich-abc")
        solo = photo(album, "solo", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p1"])
        group = photo(
            album, "group", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p1", "p2"]
        )
        album.photos = [solo, group]
        album_id = self._persist(session, album)

        # Explicit boosts so the test doesn't silently depend on the module defaults.
        result = SelectStage(StubSimilarity(), group_boost=0.01).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["group"].selected is True
        assert photos["solo"].selected is False
        assert result.selected == 1

    def test_clearly_better_solo_beats_group_shot(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # The tie-break is small: a clearly higher-aesthetic solo still wins over a
        # lower group shot (they're near-dups, so only one is picked).
        album = make_album(immich_album_id="immich-abc")
        solo = photo(album, "solo", aesthetic_score=0.9, clip_embedding=_A, person_ids=["p1"])
        group = photo(
            album, "group", aesthetic_score=0.7, clip_embedding=_A, person_ids=["p2", "p3"]
        )
        album.photos = [solo, group]
        album_id = self._persist(session, album)

        SelectStage(StubSimilarity()).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["solo"].selected is True
        assert photos["group"].selected is False

    def test_lead_guaranteed_coverage_even_as_near_dup_of_nonlead(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # The best shot is a non-lead; the lead only appears in a near-duplicate of
        # it. The lead must still be selected — never culled as a near-dup of a
        # non-lead.
        album = make_album(immich_album_id="immich-abc")
        nonlead = photo(
            album, "nonlead", aesthetic_score=0.9, clip_embedding=_A, person_ids=["p-x"]
        )
        lead = photo(
            album, "lead", aesthetic_score=0.5, clip_embedding=_A_NEAR, person_ids=["p-lead"]
        )
        album.photos = [nonlead, lead]
        self._lead(album, "p-lead")
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity(), threshold=0.9).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["nonlead"].selected is True
        assert photos["lead"].selected is True  # rescued by lead coverage
        assert result.selected == 2

    def test_multiple_leads_each_get_coverage(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # A couple: two leads whose only photos are near-duplicates of each other.
        # Each lead must be covered — the second isn't culled just for resembling
        # the first.
        album = make_album(immich_album_id="immich-abc")
        me = photo(album, "me", aesthetic_score=0.9, clip_embedding=_A, person_ids=["p-me"])
        wife = photo(
            album, "wife", aesthetic_score=0.85, clip_embedding=_A_NEAR, person_ids=["p-wife"]
        )
        album.photos = [me, wife]
        self._lead(album, "p-me", "p-wife")
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity(), threshold=0.9).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["me"].selected is True
        assert photos["wife"].selected is True
        assert result.selected == 2

    def test_lead_boost_outranks_group_tiebreak(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # All-similar scene: a single-lead photo beats a larger non-lead group shot.
        album = make_album(immich_album_id="immich-abc")
        group = photo(
            album, "group", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p1", "p2", "p3"]
        )
        lead = photo(album, "lead", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p-lead"])
        album.photos = [group, lead]
        self._lead(album, "p-lead")
        album_id = self._persist(session, album)

        # lead_boost (0.05) must beat group_boost * 3 people (0.03).
        result = SelectStage(StubSimilarity(), lead_boost=0.05, group_boost=0.01).run(
            album_id, session
        )

        photos = self._by_asset(session)
        assert photos["lead"].selected is True
        assert photos["group"].selected is False
        assert result.selected == 1

    def test_more_leads_wins_among_similar_but_original_scene_still_selected(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # Among near-duplicates, the frame with MORE leads wins (both of a couple
        # over just one). A distinct/"original" composition with fewer leads and a
        # low score is still selected — MMR keeps it for its diversity.
        album = make_album(immich_album_id="immich-abc")
        two_lead = photo(
            album, "two_lead", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p-me", "p-wife"]
        )
        one_lead = photo(
            album, "one_lead", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p-me"]
        )
        original = photo(
            album, "original", aesthetic_score=0.3, clip_embedding=_B, person_ids=["p-me"]
        )
        album.photos = [two_lead, one_lead, original]
        self._lead(album, "p-me", "p-wife")
        album_id = self._persist(session, album)

        result = SelectStage(
            StubSimilarity(), threshold=0.9, lead_boost=0.05, group_boost=0.01
        ).run(album_id, session)

        photos = self._by_asset(session)
        # Among the near-dups, the two-lead frame wins; the one-lead near-dup loses.
        assert photos["two_lead"].selected is True
        assert photos["one_lead"].selected is False
        # The dissimilar, low-score original scene is still kept for diversity.
        assert photos["original"].selected is True
        assert result.selected == 2

    def test_non_lead_seen_only_in_near_dup_stays_unselected(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # Strict posture: there is no generic coverage phase, so a non-lead person
        # appearing only in a near-duplicate of a pick is NOT rescued — the book
        # isn't padded with a look-alike just to make a face appear.
        album = make_album(immich_album_id="immich-abc")
        best = photo(album, "best", aesthetic_score=0.9, clip_embedding=_A, person_ids=["p1"])
        near = photo(album, "near", aesthetic_score=0.8, clip_embedding=_A_NEAR, person_ids=["p2"])
        album.photos = [best, near]
        album_id = self._persist(session, album)  # no leads

        result = SelectStage(StubSimilarity(), threshold=0.9).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["best"].selected is True
        assert photos["near"].selected is False  # p2 stays uncovered — guard honored
        assert result.selected == 1

    def test_group_boost_is_configurable(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # With the group tie-break disabled (group_boost=0), the earlier (lower-id)
        # solo wins the tie instead of the group shot — proving the kwarg is wired.
        album = make_album(immich_album_id="immich-abc")
        solo = photo(album, "solo", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p1"])
        group = photo(
            album, "group", aesthetic_score=0.8, clip_embedding=_A, person_ids=["p1", "p2"]
        )
        album.photos = [solo, group]
        album_id = self._persist(session, album)

        SelectStage(StubSimilarity(), group_boost=0.0).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["solo"].selected is True  # tie-break gone ⇒ deterministic id order wins
        assert photos["group"].selected is False

    def test_lead_boost_is_configurable(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # A strong non-lead and a weaker lead as near-duplicates. At the default
        # boost the non-lead ranks first (kept) and the lead is added by coverage;
        # a large lead_boost makes the lead outrank and *replace* the non-lead as
        # the scene's representative (non-leads get no coverage) — so the injected
        # boost observably changes the selection.
        album = make_album(immich_album_id="immich-abc")
        nonlead = photo(
            album, "nonlead", aesthetic_score=0.9, clip_embedding=_A, person_ids=["p-x"]
        )
        lead = photo(
            album, "lead", aesthetic_score=0.7, clip_embedding=_A_NEAR, person_ids=["p-lead"]
        )
        album.photos = [nonlead, lead]
        self._lead(album, "p-lead")
        album_id = self._persist(session, album)

        default = SelectStage(StubSimilarity(), threshold=0.9).run(album_id, session)
        photos = self._by_asset(session)
        assert (photos["nonlead"].selected, photos["lead"].selected) == (True, True)
        assert default.selected == 2

        boosted = SelectStage(StubSimilarity(), threshold=0.9, lead_boost=0.5).run(
            album_id, session
        )
        photos = self._by_asset(session)
        # The lead now outranks; the non-lead is dropped as its near-dup (no coverage).
        assert (photos["lead"].selected, photos["nonlead"].selected) == (True, False)
        assert boosted.selected == 1

    def test_face_free_album_unaffected_by_lead_config(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # Genericity guard: photos with no recognized faces select exactly by plain
        # MMR (best kept, dissimilar kept, near-dup dropped) even when the album has
        # leads configured — the people-aware phases must be inert without faces.
        album = make_album(immich_album_id="immich-abc")
        a = photo(album, "a", aesthetic_score=0.9, clip_embedding=_A)  # person_ids default None
        b = photo(album, "b", aesthetic_score=0.8, clip_embedding=_B)  # dissimilar
        c = photo(album, "c", aesthetic_score=0.7, clip_embedding=_A_NEAR)  # near-dup of a
        album.photos = [a, b, c]
        self._lead(album, "p-ghost")  # a lead present in no photo
        album_id = self._persist(session, album)

        result = SelectStage(StubSimilarity(), threshold=0.9).run(album_id, session)

        photos = self._by_asset(session)
        assert photos["a"].selected is True
        assert photos["b"].selected is True
        assert photos["c"].selected is False  # near-dup of a, dropped exactly as plain MMR
        assert result.selected == 2

    def test_lead_in_another_album_does_not_leak(
        self, session: Session, make_album: Callable[..., Album], photo: Callable[..., Photo]
    ) -> None:
        # is_lead is album-scoped: a person who is a lead in another album must not
        # be treated as a lead here (which would rescue their near-dup photo).
        other = make_album(slug="other", immich_album_id="immich-other")
        other.photos = [
            photo(other, "o1", aesthetic_score=0.9, clip_embedding=_A, person_ids=["p-shared"])
        ]
        self._lead(other, "p-shared")
        self._persist(session, other)

        target = make_album(slug="target", immich_album_id="immich-target")
        t_best = photo(target, "t_best", aesthetic_score=0.9, clip_embedding=_A, person_ids=["p-x"])
        t_shared = photo(
            target, "t_shared", aesthetic_score=0.5, clip_embedding=_A_NEAR, person_ids=["p-shared"]
        )
        target.photos = [t_best, t_shared]
        target_id = self._persist(session, target)

        result = SelectStage(StubSimilarity(), threshold=0.9).run(target_id, session)

        photos = self._by_asset(session)
        assert photos["t_best"].selected is True
        # p-shared isn't a lead in `target`, so its near-dup is NOT rescued.
        assert photos["t_shared"].selected is False
        assert result.selected == 1
