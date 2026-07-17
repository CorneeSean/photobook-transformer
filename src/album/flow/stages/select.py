"""``select`` — diversity-aware, people-aware best-of-scene via MMR, scoped by
``album_id``.

Within each [scene](/stages/cluster.md), rank candidates by an **effective
score** (highest first) — ``aesthetic_score`` plus small, tunable boosts for
priority subjects and group shots (see below) — then greedily pick a candidate
unless:

- it's an Immich-flagged duplicate (``duplicate_group_id``) of an already-picked
  photo in the same scene, or
- its CLIP cosine similarity to any already-picked photo in the scene exceeds
  ``threshold`` (near-duplicate).

**People-awareness** (Immich faces, step 17; enrichment playbook step 18) adds
three additive, opt-in behaviors that all reduce to today's MMR when a scene has
no recognized faces (``person_ids`` empty ⇒ byte-for-byte the old result):

- *Group tie-break* — the effective score adds a small ``group_boost`` per
  recognized person, so a group shot edges out an incidental one **only when
  aesthetic scores are close**; a clearly better solo still wins.
- *Lead ranking boost* — a photo gets a larger ``lead_boost`` *per lead* it
  contains (``Person.is_lead``, from ``album.yml``'s ``lead_faces`` — see
  [`lead_faces`](./lead_faces.py)), so a lead outranks the group tie-break and a
  frame with more leads (a whole couple) outranks one with fewer. Being a
  tie-break, it only orders *comparable* photos: a distinct, dissimilar
  ("original") scene is still picked on its own merits, never dropped for a
  higher-lead near-duplicate.
- *Lead guaranteed coverage* — after the greedy pass, every lead present in a
  scene is guaranteed at least one picked photo: a lead is admitted even when
  it's a near-duplicate of a **non**-lead pick (never culled for the leads' sake),
  though still not as an exact Immich dup-group repeat.
- *Strict on everyone else* — there is deliberately **no** coverage phase for
  non-leads. Honoring the near-dup guard strictly (the chosen posture — see
  knowledge/build/enrichment.md), a non-lead who appears only in near-duplicates
  of already-picked photos simply stays unselected, so the book isn't padded with
  look-alikes just to make a face appear. Only **leads** bypass that guard.

``clip_embedding`` is nullable (data-model.md) — a candidate or already-picked
photo without one simply skips the CLIP check rather than crashing the stage;
the ``duplicate_group_id`` check still applies.

Photos with no ``scene_id`` (unclustered — see [`cluster`](./cluster.py)) are
never selected. Pure logic over data already in the DB — the ``pgvector``
similarity query sits behind the substitutable :class:`~album.db.similarity.SimilaritySearch`
interface, so this module is unit-testable on in-memory SQLite with similarity
stubbed (see [build step 8](/build/build-order.md)).

Idempotent: resets ``selected`` for the album up front, so re-running recomputes
from scratch rather than compounding. :class:`SelectStage` is a
:class:`~album.flow.stages.base.Stage` (folder-structure.md rule) — it takes its
``album_id`` + ``session_factory`` at construction, opens the session inside
``run``, and is unit-testable without an orchestrator; it runs as a Prefect task via
the ``run_as_task`` / ``to_task`` glue inherited from ``TaskUnit``
(quality-practices.md §1.5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Photo
from album.db.session import SessionFactory
from album.db.similarity import SimilaritySearch
from album.flow.stages.base import Stage

_log = logging.getLogger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.9
# Small by default so people-awareness is a *tie-break* between comparable photos,
# not an override of a clearly better shot (aesthetic_score is ~0..1). A lead
# outranks the group tie-break (lead_boost > group_boost * a handful of people).
_DEFAULT_LEAD_BOOST = 0.05
_DEFAULT_GROUP_BOOST = 0.01


@dataclass
class SelectResult:
    selected: int = 0
    scenes: int = 0


class SelectStage(Stage[SelectResult]):
    """`select` — diversity-aware, people-aware best-of-scene via MMR. Idempotent.

    The ``pgvector`` similarity query sits behind the substitutable
    :class:`~album.db.similarity.SimilaritySearch`, injected here (with the
    near-duplicate ``threshold`` and the people/lead boosts as construction-time
    config) so the stage is unit-testable on in-memory SQLite with similarity
    stubbed. The ``album_id`` + ``session_factory`` are constructor arguments (base
    :class:`~album.flow.stages.base.Stage`); ``run`` takes none.
    """

    task_name: ClassVar[str] = "select"

    def __init__(
        self,
        similarity: SimilaritySearch,
        album_id: int,
        session_factory: SessionFactory,
        *,
        force: bool = False,
        threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
        lead_boost: float = _DEFAULT_LEAD_BOOST,
        group_boost: float = _DEFAULT_GROUP_BOOST,
    ) -> None:
        super().__init__(album_id, session_factory, force=force)
        self._similarity = similarity
        self._threshold = threshold
        self._lead_boost = lead_boost
        self._group_boost = group_boost

    def _process(self) -> SelectResult:
        """Pick the best, diverse photos per scene in ``self._album``. Idempotent."""
        album, session = self._album, self._session
        lead_ids = {person.immich_person_id for person in album.people if person.is_lead}

        # order_by(Photo.id): deterministic tie-breaking when effective scores are equal.
        photos = list(
            session.scalars(sa_select(Photo).where(Photo.album_id == album.id).order_by(Photo.id))
        )
        self._reset_selection(photos)
        by_scene = self._group_by_scene(photos)

        result = SelectResult(scenes=len(by_scene))
        for scene_id in sorted(by_scene):
            result.selected += self._select_scene(by_scene[scene_id], session, lead_ids)

        return result

    @staticmethod
    def _reset_selection(photos: list[Photo]) -> None:
        """Clear ``selected`` up front so a re-run recomputes rather than compounding."""
        for photo in photos:
            photo.selected = False

    @staticmethod
    def _group_by_scene(photos: list[Photo]) -> dict[int, list[Photo]]:
        """Bucket ``photos`` by ``scene_id``, dropping unclustered (``None``) ones."""
        by_scene: dict[int, list[Photo]] = {}
        for photo in photos:
            if photo.scene_id is None:  # unclustered — never selected
                continue
            by_scene.setdefault(photo.scene_id, []).append(photo)
        return by_scene

    def _select_scene(self, candidates: list[Photo], session: Session, lead_ids: set[str]) -> int:
        """MMR + people/lead coverage over one scene. Returns the number picked."""
        ranked = sorted(candidates, key=lambda p: self._effective_score(p, lead_ids), reverse=True)
        picked: list[Photo] = []
        dup_groups: set[str] = set()

        # Phase 1 — greedy MMR: pick every non-duplicate, non-near-duplicate.
        for candidate in ranked:
            self._admit(candidate, picked, dup_groups, session, similarity_against=picked)

        # Phase 2 — lead coverage: guarantee each lead present in the scene gets a
        # picked photo, bypassing the near-dup guard (a lead is never culled as a
        # near-dup). Runs once per *uncovered* lead, so distinct leads sharing a
        # near-duplicate photo (e.g. a couple) are each covered, while a second
        # near-dup of an already-covered lead is not admitted. There is
        # deliberately no equivalent phase for non-leads: honoring the guard
        # strictly, a non-lead seen only in near-dups of picks simply stays
        # unselected rather than padding the book with a look-alike.
        self._cover_leads(ranked, picked, dup_groups, session, lead_ids)

        return len(picked)

    def _effective_score(self, photo: Photo, lead_ids: set[str]) -> float:
        """Aesthetic score plus the small per-person / per-lead ranking boosts
        (see the module docstring). Exactly ``aesthetic_score`` (or -inf) when the
        photo has no recognized people, so a face-free album ranks as before."""
        base = photo.aesthetic_score if photo.aesthetic_score is not None else float("-inf")
        people = photo.person_ids or []
        num_leads = self._count_leads(people, lead_ids)
        boost = self._group_boost * len(people) + self._lead_boost * num_leads
        return base + boost

    @staticmethod
    def _count_leads(people: list[str], lead_ids: set[str]) -> int:
        """How many of ``people`` are recognized leads. Short-circuits to 0 when
        the album has no leads at all, so a lead-less album never pays for the scan."""
        if not lead_ids:
            return 0
        return sum(1 for person_id in people if person_id in lead_ids)

    def _cover_leads(
        self,
        ranked: list[Photo],
        picked: list[Photo],
        dup_groups: set[str],
        session: Session,
        lead_ids: set[str],
    ) -> None:
        for lead_id in sorted(self._people_in(ranked) & lead_ids):
            if self._covered(lead_id, picked):
                continue
            self._admit_best(lead_id, ranked, picked, dup_groups, session)

    def _admit_best(
        self,
        person_id: str,
        ranked: list[Photo],
        picked: list[Photo],
        dup_groups: set[str],
        session: Session,
    ) -> None:
        """Admit ``person_id``'s best remaining shot for guaranteed coverage.

        No similarity guard (empty guard set) — a lead's coverage is guaranteed;
        only an exact Immich dup-group repeat is skipped. ``ranked`` is highest
        effective score first, so the first match is the best shot.
        """
        for candidate in ranked:
            if person_id not in (candidate.person_ids or []):
                continue
            if self._admit(candidate, picked, dup_groups, session, similarity_against=[]):
                return

    def _admit(
        self,
        candidate: Photo,
        picked: list[Photo],
        dup_groups: set[str],
        session: Session,
        similarity_against: list[Photo],
    ) -> bool:
        """Select ``candidate`` unless a dup-group repeat or near-dup of the guard set."""
        if candidate.selected:
            return False
        if self._is_dup_group_repeat(candidate, dup_groups):
            return False
        if similarity_against and self._too_similar(candidate, similarity_against, session):
            return False
        self._record_pick(candidate, picked, dup_groups)
        return True

    @staticmethod
    def _is_dup_group_repeat(candidate: Photo, dup_groups: set[str]) -> bool:
        dup_group_id = candidate.duplicate_group_id
        return dup_group_id is not None and dup_group_id in dup_groups

    @staticmethod
    def _record_pick(candidate: Photo, picked: list[Photo], dup_groups: set[str]) -> None:
        candidate.selected = True
        picked.append(candidate)
        if candidate.duplicate_group_id is not None:
            dup_groups.add(candidate.duplicate_group_id)

    def _too_similar(self, candidate: Photo, against: list[Photo], session: Session) -> bool:
        max_sim = self._similarity.max_similarity(
            session, candidate.clip_embedding, [p.id for p in against]
        )
        return max_sim > self._threshold

    @staticmethod
    def _people_in(photos: list[Photo]) -> set[str]:
        return {person_id for photo in photos for person_id in (photo.person_ids or [])}

    @staticmethod
    def _covered(person_id: str, picked: list[Photo]) -> bool:
        return any(person_id in (photo.person_ids or []) for photo in picked)
