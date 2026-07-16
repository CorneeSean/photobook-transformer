"""``quality`` — score each photo's blur and aesthetics, scoped by ``album_id``.

Two kinds of score, written back to the [`Photo`](/reference/data-model.md) row:

- **blur** — Laplacian variance (OpenCV) over the thumbnail; low variance = blurry.
  Computed here for real (cheap, deterministic).
- **aesthetic** — a LAION-style predictor over CLIP embeddings. That model is heavy
  (torch + open_clip + weights), so it's injected behind the :class:`AestheticScorer`
  protocol rather than imported here — the stage stays unit-testable with a stub,
  exactly like the Immich client is for [`sync`](./sync.py).

Thumbnails come from Immich (via the :class:`ThumbnailSource` protocol, which
:class:`~album.immich.client.ImmichClient` satisfies) rather than re-reading
originals. Idempotent: a photo already fully scored is skipped unless ``force``.

:class:`QualityStage` itself stays Prefect-free logic (folder-structure.md rule),
so ``run`` — ``album_id`` first — is unit-testable without an orchestrator; this
module also exports :func:`quality_task`, the co-located Prefect ``@task`` that
wraps it (quality-practices.md §1.5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import cv2
import numpy as np
from prefect import task
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.flow._tasks import owns_session
from album.flow.stages.base import Stage

_log = logging.getLogger(__name__)


class ThumbnailSource(Protocol):
    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes: ...


# runtime_checkable: flow/_flow.py's pipeline() (a Prefect @flow) takes these types
# directly in its own signature. Prefect always builds a Pydantic parameter
# schema for a flow's signature (even with validate_parameters=False, which
# pipeline() sets — that only skips *validating* args against the schema,
# not building it), and that schema generation does an isinstance check on
# every annotation. (@task has no such schema/validation step at all.)
@runtime_checkable
class AestheticScorer(Protocol):
    """Scores a JPEG/PNG image's aesthetic quality (higher is better)."""

    def score(self, image: bytes) -> float: ...


@runtime_checkable
class Embedder(Protocol):
    """Produces a CLIP embedding for an image (used by cluster/select via pgvector)."""

    def embed(self, image: bytes) -> list[float]: ...


@dataclass
class QualityResult:
    scored: int = 0
    skipped: int = 0
    failed: int = 0


def compute_blur_score(image: bytes) -> float:
    """Laplacian variance of a decoded image. Raises ``ValueError`` if it won't decode."""
    buffer = np.frombuffer(image, dtype=np.uint8)
    decoded = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise ValueError("could not decode image bytes")
    return float(cv2.Laplacian(decoded, cv2.CV_64F).var())


class QualityStage(Stage[QualityResult]):
    """`quality` — score each photo's blur and aesthetics. Idempotent unless ``force``.

    Blur is computed here for real (cheap OpenCV Laplacian variance). The heavy
    pieces — the aesthetic predictor and CLIP embedder — are injected behind the
    :class:`AestheticScorer` / :class:`Embedder` protocols, alongside the
    :class:`ThumbnailSource` (Immich), so the stage is unit-testable with stubs.
    """

    def __init__(
        self,
        thumbnails: ThumbnailSource,
        aesthetic: AestheticScorer,
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self._thumbnails = thumbnails
        self._aesthetic = aesthetic
        self._embedder = embedder

    def _process(self, album: Album, session: Session, *, force: bool) -> QualityResult:
        """Score every photo in ``album``. Idempotent unless ``force``."""
        result = QualityResult()
        for photo in session.scalars(select(Photo).where(Photo.album_id == album.id)):
            self._score_photo(photo, result, force=force)

        session.flush()
        return result

    def _score_photo(self, photo: Photo, result: QualityResult, *, force: bool) -> None:
        """Score one photo, updating ``result`` in place. Skips if already scored
        (unless ``force``); isolates a scoring failure as a ``failed`` count.
        """
        if self._already_scored(photo) and not force:
            result.skipped += 1
            return

        scores = self._try_score(photo)
        if scores is None:
            result.failed += 1
            return
        # Assign only once every computation has succeeded, so a mid-photo failure
        # leaves no partial write for the trailing flush to persist (blur without
        # aesthetic would otherwise both look scored-ish and get committed).
        self._apply_scores(photo, scores)
        result.scored += 1

    @staticmethod
    def _already_scored(photo: Photo) -> bool:
        return photo.blur_score is not None and photo.aesthetic_score is not None

    @staticmethod
    def _apply_scores(photo: Photo, scores: tuple[float, float, list[float] | None]) -> None:
        blur, aesthetic_score, embedding = scores
        photo.blur_score = blur
        photo.aesthetic_score = aesthetic_score
        if embedding is not None:
            photo.clip_embedding = embedding

    def _try_score(self, photo: Photo) -> tuple[float, float, list[float] | None] | None:
        """Fetch the thumbnail and compute all scores for ``photo``, or ``None`` on failure."""
        try:
            image = self._thumbnails.get_thumbnail(photo.immich_asset_id)
            blur = compute_blur_score(image)
            aesthetic_score = self._aesthetic.score(image)
            embedding = self._embedder.embed(image) if self._embedder is not None else None
        except Exception:
            # Failure isolation: one unreadable thumbnail must not sink a 3,000-photo run.
            _log.warning("failed to score photo %s (%s)", photo.id, photo.filename, exc_info=True)
            return None
        return blur, aesthetic_score, embedding


@task(name="quality")
@owns_session
def quality_task(stage: QualityStage, album_id: int, session: Session) -> None:
    """``quality`` as a Prefect ``@task`` on its own pooled session (``owns_session``)."""
    stage.run(album_id, session)
