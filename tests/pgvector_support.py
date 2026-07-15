"""Shared helpers for the pgvector container tests.

Used by both ``test_similarity.py`` (the raw cosine query) and
``test_pgvector_integration.py`` (the migrated end-to-end suite) so the vector
builder and the container image are defined in exactly one place
(quality-practices.md §2.4 — a helper shared across files lives in one module).
"""

from __future__ import annotations

# Pinned pgvector image for every container-backed test.
PGVECTOR_IMAGE = "pgvector/pgvector:pg17"

# The pipeline's CLIP embedding width (data-model.md's Vector(768)).
_DIM = 768


def vector(*nonzero: tuple[int, float]) -> list[float]:
    """A 768-dim embedding that is zero except at the given ``(index, value)``
    positions — lets a test set just the couple of components that make the cosine
    similarity easy to reason about."""
    v = [0.0] * _DIM
    for index, value in nonzero:
        v[index] = value
    return v
