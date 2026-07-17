"""Tests for :class:`~album.flow.stages.caption.CaptionStage` (build step 12).

Per caption.md / architecture.md's shared-analysis design: this stage's whole job is
to guarantee every *selected* photo ends up with a stored caption, routing through
the shared :class:`~album.flow.photo_analyzer.PhotoAnalyzer` rather than reimplementing
it, so a photo is analyzed at most once across the whole pipeline. Covers: no second
call for a photo already analyzed by ``enhance``; a segment-less album still gets a
caption (genericity guard); ``caption`` running *before* ``enhance`` still triggers
the shared call (order independence); per-photo failure isolation; album scoping and
unknown-album handling.

The captioner is a :class:`~tests.doubles.RecordingCaptioner` (a deterministic fake
that counts calls) rather than a real model — the live-caption spot-check stays
deferred (no ``ANTHROPIC_API_KEY``; see progress.md).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.analysis.captioner import Captioner
from album.db.models import Album, Photo, Segment
from album.db.session import SessionFactory, reuse
from album.flow.photo_analyzer import PhotoAnalyzer, ThumbnailSource
from album.flow.stages.caption import CaptionResult, CaptionStage
from tests.doubles import FakeThumbnails, RecordingCaptioner, ZeroToneEstimator


class RaisingThumbnails:
    """Fetches fine except for the asset id ``"bad"``, which raises."""

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        if asset_id == "bad":
            raise RuntimeError("boom")
        return b"img"


class TestCaptionStage:
    """`CaptionStage.run`: one shared call per photo, order-independent, isolated."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def _stage(
        self,
        thumbnails: ThumbnailSource,
        captioner: Captioner,
        album_id: int,
        session_factory: SessionFactory,
        *,
        force: bool = False,
    ) -> CaptionStage:
        return CaptionStage(
            PhotoAnalyzer(thumbnails, tone_estimator=ZeroToneEstimator(), captioner=captioner),
            album_id,
            session_factory,
            force=force,
        )

    def _mark_analyzed(self, photo: Photo, caption: str) -> None:
        """Simulate a prior analysis run: all five shared fields populated."""
        photo.enhance_exposure_ev = 0.1
        photo.enhance_temp_shift_k = 50.0
        photo.enhance_shadows = 0.0
        photo.enhance_highlights = 0.0
        photo.caption = caption

    def test_does_not_repeat_call_for_already_analyzed_photo(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True)]
        album_id = self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()
        self._mark_analyzed(photo, "Already captioned.")
        session.flush()

        captioner = RecordingCaptioner()
        result = self._stage(FakeThumbnails(), captioner, album_id, reuse(session)).run()

        assert captioner.calls == []  # no analysis performed
        assert isinstance(result, CaptionResult)
        assert result.skipped == 1
        assert result.captioned == 0
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.caption == "Already captioned."

    def test_segment_less_album_still_gets_captioned(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")  # no segments
        album.photos = [
            make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True, segment=None),
        ]
        album_id = self._persist(session, album)

        result = self._stage(FakeThumbnails(), RecordingCaptioner(), album_id, reuse(session)).run()

        assert result.captioned == 1
        assert result.failed == 0
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.caption

    def test_with_segment_context_still_works(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
        make_segment: Callable[..., Segment],
    ) -> None:
        album = make_album(slug="test-album")
        segment = make_segment(album, label="Day 1", place="Lisbon")
        album.segments = [segment]
        album.photos = [
            make_photo(
                album,
                immich_asset_id="a1",
                filename="a1.jpg",
                selected=True,
                segment=segment,
            ),
        ]
        album_id = self._persist(session, album)

        result = self._stage(FakeThumbnails(), RecordingCaptioner(), album_id, reuse(session)).run()

        assert result.captioned == 1
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.caption

    def test_running_before_enhance_triggers_shared_call(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True)]
        album_id = self._persist(session, album)

        result = self._stage(
            FakeThumbnails({"a1": b"img"}),
            RecordingCaptioner(),
            album_id,
            reuse(session),
        ).run()

        assert result.captioned == 1
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.caption == "A generic caption."
        # The tone-delta columns enhance owns are populated too, since it's the same
        # underlying analysis/row write via PhotoAnalyzer.ensure (ZeroToneEstimator).
        assert photo.enhance_exposure_ev == 0.0

    def test_after_enhance_reads_back_without_second_call(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True)]
        album_id = self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        # A prior analysis run (as enhance would trigger).
        PhotoAnalyzer(
            FakeThumbnails({"a1": b"img"}),
            tone_estimator=ZeroToneEstimator(),
            captioner=RecordingCaptioner(),
        ).ensure(session, photo)
        session.flush()

        captioner = RecordingCaptioner()
        result = self._stage(
            FakeThumbnails({"a1": b"img"}), captioner, album_id, reuse(session)
        ).run()

        assert captioner.calls == []
        assert result.skipped == 1
        assert result.captioned == 0

    def test_skips_unselected_photos(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=False)]
        album_id = self._persist(session, album)

        result = self._stage(FakeThumbnails(), RecordingCaptioner(), album_id, reuse(session)).run()

        assert result.captioned == 0
        assert result.skipped == 0
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.caption is None

    def test_isolates_per_photo_failure(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(album, immich_asset_id="good", filename="good.jpg", selected=True),
            make_photo(album, immich_asset_id="bad", filename="bad.jpg", selected=True),
        ]
        album_id = self._persist(session, album)

        result = self._stage(
            RaisingThumbnails(),
            RecordingCaptioner(),
            album_id,
            reuse(session),
        ).run()

        assert result.captioned == 1
        assert result.failed == 1
        photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
        assert photos["good"].caption
        assert photos["bad"].caption is None

    def test_scoped_to_album(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        other_album = make_album(slug="other")
        other_album.photos = [
            make_photo(other_album, immich_asset_id="o1", filename="o1.jpg", selected=True),
        ]
        other_id = self._persist(session, other_album)
        target_album = make_album(slug="target")
        target_album.photos = [
            make_photo(target_album, immich_asset_id="t1", filename="t1.jpg", selected=True),
        ]
        target_id = self._persist(session, target_album)

        self._stage(FakeThumbnails(), RecordingCaptioner(), target_id, reuse(session)).run()

        other_photo = session.scalars(sa_select(Photo).where(Photo.album_id == other_id)).one()
        assert other_photo.caption is None
        target_photo = session.scalars(sa_select(Photo).where(Photo.album_id == target_id)).one()
        assert target_photo.caption is not None

    def test_raises_for_unknown_album(self, session: Session) -> None:
        with pytest.raises(ValueError):
            self._stage(FakeThumbnails(), RecordingCaptioner(), 999, reuse(session)).run()

    def test_force_reanalyzes_already_captioned_photo(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True)]
        album_id = self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()
        self._mark_analyzed(photo, "Old caption.")
        session.flush()

        result = self._stage(
            FakeThumbnails({"a1": b"img"}),
            RecordingCaptioner("A fresh caption."),
            album_id,
            reuse(session),
            force=True,
        ).run()

        assert result.captioned == 1
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.caption == "A fresh caption."
