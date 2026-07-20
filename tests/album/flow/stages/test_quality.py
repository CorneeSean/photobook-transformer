"""Tests for :class:`~album.flow.stages.quality.QualityStage` over in-memory SQLite.

Blur is scored for real against committed fixture images (a sharp photo and a
pre-blurred copy of the *same* source, from Lorem Picsum). The aesthetic model is
heavy, so it's stubbed via a fake :class:`AestheticScorer`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.db.session import reuse
from album.flow.stages.base import StageTotalFailure
from album.flow.stages.quality import QualityResult, QualityStage, compute_blur_score
from tests.doubles import ConstantAesthetic, ConstantEmbedder

_IMAGES = Path(__file__).parent.parent.parent.parent / "fixtures" / "images"
_SHARP = (_IMAGES / "sharp.jpg").read_bytes()
_BLURRY = (_IMAGES / "blurry.jpg").read_bytes()


class FakeThumbnails:
    """Serves canned thumbnail bytes keyed by asset id."""

    def __init__(self, by_asset: dict[str, bytes], default: bytes = _SHARP) -> None:
        self._by_asset = by_asset
        self._default = default
        self.calls: list[str] = []

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        self.calls.append(asset_id)
        return self._by_asset.get(asset_id, self._default)


class RaisingAesthetic:
    def score(self, image: bytes) -> float:
        raise RuntimeError("model unavailable")


class RaiseForImage:
    """Scores every image except one poison payload, for which it raises — so a
    single photo can fail among others that succeed (avoiding total-failure
    escalation, which is covered separately)."""

    def __init__(self, poison: bytes) -> None:
        self._poison = poison

    def score(self, image: bytes) -> float:
        if image == self._poison:
            raise RuntimeError("model unavailable for this image")
        return 0.7


class SharedQualityModel:
    """One object backing **both** :class:`AestheticScorer` and :class:`Embedder`,
    mirroring how the real ``ClipAestheticModel`` (and the ``_UnavailableQualityModel``
    placeholder) is injected as both collaborators. Records each call so the stage's
    use of the single shared instance for score *and* embed is observable."""

    def __init__(self) -> None:
        self.scored = 0
        self.embedded = 0

    def score(self, image: bytes) -> float:
        self.scored += 1
        return 0.6

    def embed(self, image: bytes) -> list[float]:
        self.embedded += 1
        return [0.2] * 768


def test_compute_blur_score_ranks_sharp_above_blurry() -> None:
    # A pure module-level transform (quality-practices §1.1) — tested directly.
    assert compute_blur_score(_SHARP) > compute_blur_score(_BLURRY)


class TestQuality:
    """`QualityStage.run`: scoring, idempotency, scoping, and per-photo isolation."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def test_scores_land_in_the_db(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        album.photos = [
            make_photo(album, immich_asset_id="sharp", filename="sharp.jpg"),
            make_photo(album, immich_asset_id="blurry", filename="blurry.jpg"),
        ]
        album_id = self._persist(session, album)
        thumbs = FakeThumbnails({"sharp": _SHARP, "blurry": _BLURRY})

        result = QualityStage(thumbs, ConstantAesthetic(0.7), album_id, reuse(session)).run()

        assert result == QualityResult(scored=2)
        photos = {p.immich_asset_id: p for p in session.scalars(select(Photo))}
        assert photos["sharp"].blur_score is not None
        assert photos["sharp"].aesthetic_score == 0.7
        # Real blur scoring preserves the sharp > blurry ordering end-to-end.
        assert photos["sharp"].blur_score > photos["blurry"].blur_score  # pyright: ignore[reportOperatorIssue]

    def test_is_idempotent_and_skips_already_scored(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        album.photos = [make_photo(album, immich_asset_id="a1")]
        album_id = self._persist(session, album)
        thumbs = FakeThumbnails({})
        aesthetic = ConstantAesthetic()

        first = QualityStage(thumbs, aesthetic, album_id, reuse(session)).run()
        second = QualityStage(thumbs, aesthetic, album_id, reuse(session)).run()

        assert first == QualityResult(scored=1)
        assert second == QualityResult(skipped=1)
        assert aesthetic.calls == 1  # not re-scored on the second run
        assert thumbs.calls == ["a1"]  # thumbnail fetched only once

    def test_force_rescores(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        album.photos = [make_photo(album, immich_asset_id="a1")]
        album_id = self._persist(session, album)
        aesthetic = ConstantAesthetic()

        QualityStage(FakeThumbnails({}), aesthetic, album_id, reuse(session)).run()
        result = QualityStage(
            FakeThumbnails({}),
            aesthetic,
            album_id,
            reuse(session),
            force=True,
        ).run()

        assert result == QualityResult(scored=1)
        assert aesthetic.calls == 2

    def test_scoped_to_album(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        other = make_album(slug="other", immich_album_id="immich-other")
        other.photos = [make_photo(other, immich_asset_id="other-1")]
        other_id = self._persist(session, other)
        target = make_album(slug="target", immich_album_id="immich-target")
        target.photos = [make_photo(target, immich_asset_id="target-1")]
        target_id = self._persist(session, target)

        QualityStage(FakeThumbnails({}), ConstantAesthetic(), target_id, reuse(session)).run()

        other_photo = session.scalars(select(Photo).where(Photo.album_id == other_id)).one()
        assert other_photo.blur_score is None  # untouched
        target_photo = session.scalars(select(Photo).where(Photo.album_id == target_id)).one()
        assert target_photo.blur_score is not None

    def test_failure_after_blur_leaves_no_partial_write(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # If aesthetic scoring raises *after* blur is computed, neither score is
        # persisted — the write is atomic per photo. A second, succeeding photo keeps
        # this from being a total failure (that path is covered by
        # test_all_photos_failing_escalates).
        album = make_album(immich_album_id="immich-abc")
        album.photos = [
            make_photo(album, immich_asset_id="good", filename="good.jpg"),
            make_photo(album, immich_asset_id="bad", filename="bad.jpg"),
        ]
        album_id = self._persist(session, album)
        thumbs = FakeThumbnails({"good": _SHARP, "bad": _BLURRY})

        result = QualityStage(thumbs, RaiseForImage(_BLURRY), album_id, reuse(session)).run()

        assert result == QualityResult(scored=1, failed=1)
        bad = session.scalars(select(Photo).where(Photo.immich_asset_id == "bad")).one()
        assert bad.blur_score is None
        assert bad.aesthetic_score is None

    def test_all_photos_failing_escalates(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # A systemic failure (aesthetic model unavailable for every photo) must
        # escalate rather than report a completed stage — see step 41.
        album = make_album(immich_album_id="immich-abc")
        album.photos = [
            make_photo(album, immich_asset_id="a1", filename="a1.jpg"),
            make_photo(album, immich_asset_id="a2", filename="a2.jpg"),
        ]
        album_id = self._persist(session, album)

        with pytest.raises(StageTotalFailure, match="quality: failed for all 2 attempted"):
            QualityStage(FakeThumbnails({}), RaisingAesthetic(), album_id, reuse(session)).run()

    def test_computes_embedding_when_embedder_given(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        album.photos = [make_photo(album, immich_asset_id="a1")]
        album_id = self._persist(session, album)
        embedder = ConstantEmbedder()

        QualityStage(
            FakeThumbnails({}),
            ConstantAesthetic(),
            album_id,
            reuse(session),
            embedder=embedder,
        ).run()

        assert embedder.calls == 1
        photo = session.scalars(select(Photo)).one()
        assert photo.clip_embedding is not None
        assert len(photo.clip_embedding) == 768

    def test_uses_one_shared_instance_for_both_score_and_embed(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # The flow wires one model as both the aesthetic scorer and the embedder
        # (build step 31). Injecting the same object as both here proves the stage
        # drives score() and embed() on that single shared instance.
        album = make_album(immich_album_id="immich-abc")
        album.photos = [make_photo(album, immich_asset_id="a1")]
        album_id = self._persist(session, album)
        model = SharedQualityModel()

        QualityStage(FakeThumbnails({}), model, album_id, reuse(session), embedder=model).run()

        assert model.scored == 1
        assert model.embedded == 1
        photo = session.scalars(select(Photo)).one()
        assert photo.aesthetic_score == 0.6
        assert photo.clip_embedding is not None

    def test_undecodable_thumbnail_is_isolated_not_fatal(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(immich_album_id="immich-abc")
        album.photos = [
            make_photo(album, immich_asset_id="good"),
            make_photo(album, immich_asset_id="bad"),
        ]
        album_id = self._persist(session, album)
        thumbs = FakeThumbnails({"good": _SHARP, "bad": b"not-an-image"})

        result = QualityStage(thumbs, ConstantAesthetic(), album_id, reuse(session)).run()

        assert result == QualityResult(scored=1, failed=1)
        photos = {p.immich_asset_id: p for p in session.scalars(select(Photo))}
        assert photos["good"].blur_score is not None
        assert photos["bad"].blur_score is None  # left unscored, run continued
