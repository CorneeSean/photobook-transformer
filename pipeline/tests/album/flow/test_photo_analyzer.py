"""Tests for :class:`~album.flow.photo_analyzer.PhotoAnalyzer` — the shared per-photo
analysis trigger (step 29 split it into a tone estimator + a captioner).

``ensure`` is the single point that runs (and persists) a photo's analysis, at most
once; both ``EnhanceStage`` and ``CaptionStage`` route through it. Covers: it persists
all five fields and returns the raw tone delta; the no-double-analysis contract; the
offline default (no model); grounding reaching a ``ClaudeCaptioner`` prompt; and the
per-run A/B path (re-caption under ``force`` with a different captioner, tone
unchanged because it's deterministic).
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.analysis.captioner import ClaudeCaptioner
from album.analysis.tone import ToneParams
from album.db.models import Album, Person, Photo
from album.flow.photo_analyzer import PhotoAnalyzer
from tests.doubles import (
    FakeThumbnails,
    FixedTone,
    RecordingCaptioner,
    ZeroToneEstimator,
    capturing_caption_model,
)

_DECODABLE_IMAGE = cv2.imencode(".png", np.full((8, 8, 3), 128, np.uint8))[1].tobytes()


def _persist(session: Session, album: Album) -> None:
    session.add(album)
    session.commit()
    assert album.id is not None


class TestPhotoAnalyzer:
    """`PhotoAnalyzer.ensure`: persists all fields, and never analyzes twice."""

    def test_persists_all_fields_and_returns_the_raw_tone(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        tone = ToneParams(exposure_ev=0.2, temp_shift_k=100.0, shadows=0.1, highlights=-0.1)
        analyzer = PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=FixedTone(tone),
            captioner=RecordingCaptioner("A test photo."),
        )
        result = analyzer.ensure(session, photo)

        assert result == tone  # the raw delta is returned for enhance to reconcile
        assert photo.enhance_exposure_ev == 0.2
        assert photo.enhance_temp_shift_k == 100.0
        assert photo.enhance_shadows == 0.1
        assert photo.enhance_highlights == -0.1
        assert photo.caption == "A test photo."

    def test_is_not_repeated_for_an_already_populated_photo(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        captioner = RecordingCaptioner()
        thumbnails = FakeThumbnails()
        analyzer = PhotoAnalyzer(
            thumbnails, tone_estimator=ZeroToneEstimator(), captioner=captioner
        )

        first = analyzer.ensure(session, photo)
        second = analyzer.ensure(session, photo)

        assert first is not None
        assert second is None  # no-op: already analyzed
        assert len(captioner.calls) == 1  # captioner ran exactly once
        assert thumbnails.calls == ["a1"]  # thumbnail fetched only once

    def test_offline_default_needs_no_model(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # The headline win: the default analyzer (classical tone + template caption)
        # captions a photo with no model and no ANTHROPIC_API_KEY.
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        result = PhotoAnalyzer(FakeThumbnails(default=_DECODABLE_IMAGE)).ensure(session, photo)

        assert result is not None
        assert photo.caption

    def test_handles_no_segment_context_without_erroring(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()  # no segments
        album.photos = [make_photo(album, immich_asset_id="a1", segment=None)]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        analyzer = PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=RecordingCaptioner(),
        )
        result = analyzer.ensure(session, photo)

        assert result is not None
        assert photo.caption

    def test_force_reanalyzes_a_populated_photo(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        captioner = RecordingCaptioner()
        analyzer = PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=captioner,
        )

        analyzer.ensure(session, photo)
        forced = analyzer.ensure(session, photo, force=True)

        assert forced is not None
        assert len(captioner.calls) == 2  # analyzed again under force


class TestForceRecaption:
    """Per-run A/B: re-run an already-captioned photo with a *different* captioner
    under ``force`` — the caption swaps, and the (deterministic classical) tone is
    unchanged, so the A/B axis is isolated to the caption."""

    def test_force_overwrites_caption_keeps_tone(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        tone = FixedTone(ToneParams(exposure_ev=0.3, temp_shift_k=0.0, shadows=0.0, highlights=0.0))
        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=tone,
            captioner=RecordingCaptioner("template caption"),
        ).ensure(session, photo)
        assert photo.caption == "template caption"
        tone_before = ToneParams.from_photo(photo)

        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=tone,
            captioner=RecordingCaptioner("claude caption"),
        ).ensure(session, photo, force=True)

        assert photo.caption == "claude caption"  # the A/B swap took effect
        assert ToneParams.from_photo(photo) == tone_before  # tone unchanged (isolated)


class TestPeopleGrounding:
    """Step 19: a photo's *named* people are resolved (via the album-scoped ``Person``
    cache) and reach the ``ClaudeCaptioner`` prompt — opt-in, face-free unchanged."""

    def test_named_people_reach_the_prompt(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.people.append(Person(immich_person_id="p-alex", name="Alex"))
        album.people.append(Person(immich_person_id="p-sam", name="Sam"))
        album.photos = [make_photo(album, immich_asset_id="a1", person_ids=["p-alex", "p-sam"])]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_caption_model()
        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=ClaudeCaptioner(model=model),
        ).ensure(session, photo)

        joined = "\n".join(prompts)
        assert "Alex" in joined
        assert "Sam" in joined

    def test_unnamed_and_unknown_people_are_dropped(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.people.append(Person(immich_person_id="p-alex", name="Alex"))
        album.people.append(Person(immich_person_id="p-unnamed", name=None))
        album.photos = [
            make_photo(album, immich_asset_id="a1", person_ids=["p-alex", "p-unnamed", "p-ghost"]),
        ]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_caption_model()
        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=ClaudeCaptioner(model=model),
        ).ensure(session, photo)

        joined = "\n".join(prompts)
        assert "Alex" in joined
        assert "None" not in joined
        assert "p-ghost" not in joined
        assert "p-unnamed" not in joined

    def test_face_free_photo_adds_no_people_line(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1", person_ids=None)]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_caption_model()
        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=ClaudeCaptioner(model=model),
        ).ensure(session, photo)

        assert "People in this photo" not in "\n".join(prompts)


class TestPlaceGrounding:
    """Step 20: a photo's reverse-geocoded place reaches the ``ClaudeCaptioner``
    prompt as caption grounding — opt-in, and a place-less photo is unchanged."""

    def test_geocoded_place_reaches_prompt(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [
            make_photo(
                album,
                immich_asset_id="a1",
                city="Lisbon",
                state="Lisbon Municipality",
                country="Portugal",
            ),
        ]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_caption_model()
        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=ClaudeCaptioner(model=model),
        ).ensure(session, photo)

        assert "Lisbon, Lisbon Municipality, Portugal" in "\n".join(prompts)

    def test_no_geocoded_place_adds_no_location_line(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1")]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_caption_model()
        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=ClaudeCaptioner(model=model),
        ).ensure(session, photo)

        assert "Location:" not in "\n".join(prompts)

    def test_duplicate_place_parts_deduped(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [
            make_photo(
                album,
                immich_asset_id="a1",
                city="Singapore",
                state="Singapore",
                country="Singapore",
            ),
        ]
        _persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_caption_model()
        PhotoAnalyzer(
            FakeThumbnails(),
            tone_estimator=ZeroToneEstimator(),
            captioner=ClaudeCaptioner(model=model),
        ).ensure(session, photo)

        assert "Location: Singapore." in "\n".join(prompts)
