"""CLIP cosine-similarity search behind a substitutable interface.

MMR selection ([`stages/select.py`](../stages/select.py)) needs "how similar is
this candidate to an already-picked photo," without caring whether that's
answered by a real `pgvector` query or a stub. `pgvector` doesn't exist in
SQLite (data-model.md), so pure-logic `select` tests stub :class:`SimilaritySearch`;
the real query here is covered separately against a Postgres test container
(build step 8).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Photo


# runtime_checkable: flow.py's pipeline() (a Prefect @flow) takes this type
# directly in its own signature. Prefect always builds a Pydantic parameter
# schema for a flow's signature (even with validate_parameters=False, which
# pipeline() sets — that only skips *validating* args against the schema,
# not building it), and that schema generation does an isinstance check on
# every annotation. (@task has no such schema/validation step at all.)
@runtime_checkable
class SimilaritySearch(Protocol):
    def max_similarity(
        self,
        session: Session,
        embedding: Sequence[float] | None,
        against: Sequence[int],
    ) -> float:
        """Highest cosine similarity between ``embedding`` and any already-picked
        photo id in ``against``. Returns ``-1.0`` (below any real similarity) if
        ``embedding`` is ``None``, ``against`` is empty, or none of ``against``
        has an embedding to compare against — ``clip_embedding`` is nullable
        (data-model.md), so a candidate a caller can't score similarity for must
        not crash the stage; it's simply treated as unlike anything picked so
        far."""
        ...


class PgVectorSimilarity:
    """Real similarity search: pgvector's cosine-distance operator, index-accelerated."""

    def max_similarity(
        self,
        session: Session,
        embedding: Sequence[float] | None,
        against: Sequence[int],
    ) -> float:
        if embedding is None or not against:
            return -1.0
        distances = session.scalars(
            select(Photo.clip_embedding.cosine_distance(embedding))
            .where(Photo.id.in_(against))
            .where(Photo.clip_embedding.is_not(None)),
        ).all()
        return 1.0 - min(distances) if distances else -1.0
