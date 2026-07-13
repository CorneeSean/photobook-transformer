"""Tests for the ``quality`` stage over in-memory SQLite.

Blur is scored for real against committed fixture images (a sharp photo and a
pre-blurred copy of the *same* source, from Lorem Picsum). The aesthetic model is
heavy, so it's stubbed via a fake :class:`AestheticScorer`.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.stages.quality import QualityResult, compute_blur_score, quality

from .conftest import make_album, make_photo

_IMAGES = Path(__file__).parent / "fixtures" / "images"
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


class ConstantAesthetic:
    def __init__(self, value: float = 0.5) -> None:
        self.value = value
        self.calls = 0

    def score(self, image: bytes) -> float:
        self.calls += 1
        return self.value


def _persist(session: Session, album: Album) -> int:
    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


def test_compute_blur_score_ranks_sharp_above_blurry() -> None:
    assert compute_blur_score(_SHARP) > compute_blur_score(_BLURRY)


def test_scores_land_in_the_db(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        make_photo(album, immich_asset_id="sharp", filename="sharp.jpg"),
        make_photo(album, immich_asset_id="blurry", filename="blurry.jpg"),
    ]
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({"sharp": _SHARP, "blurry": _BLURRY})

    result = quality(album_id, session, thumbs, ConstantAesthetic(0.7))

    assert result == QualityResult(scored=2)
    photos = {p.immich_asset_id: p for p in session.scalars(select(Photo))}
    assert photos["sharp"].blur_score is not None
    assert photos["sharp"].aesthetic_score == 0.7
    # The stage's real blur scoring preserves the sharp > blurry ordering end-to-end.
    assert photos["sharp"].blur_score > photos["blurry"].blur_score  # pyright: ignore[reportOperatorIssue]


def test_is_idempotent_and_skips_already_scored(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [make_photo(album, immich_asset_id="a1")]
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({})
    aesthetic = ConstantAesthetic()

    first = quality(album_id, session, thumbs, aesthetic)
    second = quality(album_id, session, thumbs, aesthetic)

    assert first == QualityResult(scored=1)
    assert second == QualityResult(skipped=1)
    assert aesthetic.calls == 1  # not re-scored on the second run
    assert thumbs.calls == ["a1"]  # thumbnail fetched only once


def test_force_rescores(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [make_photo(album, immich_asset_id="a1")]
    album_id = _persist(session, album)
    aesthetic = ConstantAesthetic()

    quality(album_id, session, FakeThumbnails({}), aesthetic)
    result = quality(album_id, session, FakeThumbnails({}), aesthetic, force=True)

    assert result == QualityResult(scored=1)
    assert aesthetic.calls == 2


def test_scoped_to_album(session: Session) -> None:
    other = make_album(slug="other", immich_album_id="immich-other")
    other.photos = [make_photo(other, immich_asset_id="other-1")]
    other_id = _persist(session, other)
    target = make_album(slug="target", immich_album_id="immich-target")
    target.photos = [make_photo(target, immich_asset_id="target-1")]
    target_id = _persist(session, target)

    quality(target_id, session, FakeThumbnails({}), ConstantAesthetic())

    other_photo = session.scalars(select(Photo).where(Photo.album_id == other_id)).one()
    assert other_photo.blur_score is None  # untouched
    target_photo = session.scalars(select(Photo).where(Photo.album_id == target_id)).one()
    assert target_photo.blur_score is not None


class RaisingAesthetic:
    def score(self, image: bytes) -> float:
        raise RuntimeError("model unavailable")


class ConstantEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, image: bytes) -> list[float]:
        self.calls += 1
        return [0.1] * 768


def test_failure_after_blur_leaves_no_partial_write(session: Session) -> None:
    # If aesthetic scoring raises *after* blur is computed, neither score is
    # persisted — the write is atomic per photo.
    album = make_album(immich_album_id="immich-abc")
    album.photos = [make_photo(album, immich_asset_id="a1")]
    album_id = _persist(session, album)

    result = quality(album_id, session, FakeThumbnails({}), RaisingAesthetic())

    assert result == QualityResult(failed=1)
    photo = session.scalars(select(Photo)).one()
    assert photo.blur_score is None
    assert photo.aesthetic_score is None


def test_computes_embedding_when_embedder_given(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [make_photo(album, immich_asset_id="a1")]
    album_id = _persist(session, album)
    embedder = ConstantEmbedder()

    quality(album_id, session, FakeThumbnails({}), ConstantAesthetic(), embedder=embedder)

    assert embedder.calls == 1
    photo = session.scalars(select(Photo)).one()
    assert photo.clip_embedding is not None
    assert len(photo.clip_embedding) == 768


def test_undecodable_thumbnail_is_isolated_not_fatal(session: Session) -> None:
    album = make_album(immich_album_id="immich-abc")
    album.photos = [
        make_photo(album, immich_asset_id="good"),
        make_photo(album, immich_asset_id="bad"),
    ]
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({"good": _SHARP, "bad": b"not-an-image"})

    result = quality(album_id, session, thumbs, ConstantAesthetic())

    assert result == QualityResult(scored=1, failed=1)
    photos = {p.immich_asset_id: p for p in session.scalars(select(Photo))}
    assert photos["good"].blur_score is not None
    assert photos["bad"].blur_score is None  # left unscored, run continued
