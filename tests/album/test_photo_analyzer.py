"""Tests for :class:`~album.photo_analyzer.PhotoAnalyzer` — the shared
"one combined vision call" trigger.

``ensure`` is the single point that runs (and persists) the vision call for a
photo, exactly once; both ``EnhanceStage`` and ``CaptionStage`` route through it.
Its no-double-call contract is tested explicitly with PydanticAI's
``TestModel``/``FunctionModel`` (build-order step 10).
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.photo_analyzer import PhotoAnalyzer
from tests.doubles import FakeThumbnails, analysis_test_model, counting_analysis_model


class TestPhotoAnalyzer:
    """`PhotoAnalyzer.ensure`: persists all fields, and never analyzes twice."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def test_calls_agent_and_persists_all_fields(
        self, session: Session, make_album: Callable[..., Album], make_photo: Callable[..., Photo]
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        analyzer = PhotoAnalyzer(
            FakeThumbnails(), model=analysis_test_model(caption="A test photo.")
        )
        result = analyzer.ensure(session, photo)

        assert result is not None
        assert photo.enhance_exposure_ev == result.exposure_ev
        assert photo.enhance_temp_shift_k == result.temp_shift_k
        assert photo.enhance_shadows == result.shadows
        assert photo.enhance_highlights == result.highlights
        assert photo.caption == result.caption

    def test_is_not_repeated_for_an_already_populated_photo(
        self, session: Session, make_album: Callable[..., Album], make_photo: Callable[..., Photo]
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, calls = counting_analysis_model()
        thumbnails = FakeThumbnails()
        analyzer = PhotoAnalyzer(thumbnails, model=model)

        first = analyzer.ensure(session, photo)
        second = analyzer.ensure(session, photo)

        assert first is not None
        assert second is None  # no-op: already analyzed
        assert calls == [1]  # the model was called exactly once
        assert thumbnails.calls == ["a1"]  # thumbnail fetched only once

    def test_handles_no_segment_context_without_erroring(
        self, session: Session, make_album: Callable[..., Album], make_photo: Callable[..., Photo]
    ) -> None:
        album = make_album()  # no segments
        album.photos = [make_photo(album, immich_asset_id="a1", segment=None)]
        self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        analyzer = PhotoAnalyzer(FakeThumbnails(), model=analysis_test_model())
        result = analyzer.ensure(session, photo)

        assert result is not None
        assert photo.caption

    def test_force_reanalyzes_a_populated_photo(
        self, session: Session, make_album: Callable[..., Album], make_photo: Callable[..., Photo]
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, calls = counting_analysis_model()
        analyzer = PhotoAnalyzer(FakeThumbnails(), model=model)

        analyzer.ensure(session, photo)
        forced = analyzer.ensure(session, photo, force=True)

        assert forced is not None
        assert calls == [1, 1]  # called again under force
