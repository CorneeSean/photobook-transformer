"""Tests for the ``caption`` stage (build step 12).

Per caption.md / architecture.md's "one combined vision call": this stage's
whole job is to guarantee every *selected* photo ends up with a stored
caption, reusing ``enhance.ensure_analysis`` — the shared idempotent trigger —
rather than reimplementing it, so there is exactly one API round-trip per
photo across the whole pipeline. Covers: no second call for a photo already
analyzed by ``enhance``; a segment-less album still gets a caption
(genericity guard); ``caption`` running *before* ``enhance`` still triggers
the shared call (order independence); per-photo failure isolation; album
scoping and unknown-album handling, mirroring quality.py's/enhance.py's own
tests.

Manual spot-check of ~20 captions for accuracy/non-genericity (build-order
step 12) is deferred — no ``ANTHROPIC_API_KEY`` in this environment; see
progress.md.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.stages.caption import CaptionResult, caption

from .conftest import make_album, make_photo, make_segment


class FakeThumbnails:
    def __init__(self, by_asset: dict[str, bytes] | None = None, default: bytes = b"img") -> None:
        self._by_asset = by_asset or {}
        self._default = default
        self.calls: list[str] = []

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        self.calls.append(asset_id)
        return self._by_asset.get(asset_id, self._default)


def _valid_result_model() -> TestModel:
    return TestModel(
        custom_output_args={
            "exposure_ev": 0.0,
            "temp_shift_k": 0.0,
            "shadows": 0.0,
            "highlights": 0.0,
            "caption": "A generic caption.",
        }
    )


def _counting_model() -> tuple[FunctionModel, list[int]]:
    calls: list[int] = []

    def handler(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
        calls.append(1)
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        '{"exposure_ev": 0.0, "temp_shift_k": 0.0, "shadows": 0.0, '
                        '"highlights": 0.0, "caption": "A generic caption."}'
                    )
                )
            ]
        )

    return FunctionModel(handler), calls


def _persist(session: Session, album: Album) -> int:
    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


# --- No second API call when enhance already ran ---------------------------


def test_caption_does_not_repeat_call_for_already_analyzed_photo(session: Session) -> None:
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(
            album,
            immich_asset_id="a1",
            filename="a1.jpg",
            selected=True,
        ),
    ]
    album_id = _persist(session, album)
    photo = session.scalars(sa_select(Photo)).one()
    # Simulate a prior `enhance` run: all 5 shared-analysis fields populated.
    photo.enhance_exposure_ev = 0.1
    photo.enhance_temp_shift_k = 50.0
    photo.enhance_shadows = 0.0
    photo.enhance_highlights = 0.0
    photo.caption = "Already captioned."
    session.flush()

    model, calls = _counting_model()
    result = caption(album_id, session, FakeThumbnails(), model)

    assert calls == []  # no API call made
    assert isinstance(result, CaptionResult)
    assert result.skipped == 1
    assert result.captioned == 0
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.caption == "Already captioned."


# --- Genericity guard: segment-less album -----------------------------------


def test_caption_segment_less_album_still_gets_captioned(session: Session) -> None:
    album = make_album(slug="test-album")  # no segments
    album.photos = [
        make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True, segment=None),
    ]
    album_id = _persist(session, album)

    result = caption(album_id, session, FakeThumbnails(), _valid_result_model())

    assert result.captioned == 1
    assert result.failed == 0
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.caption


def test_caption_with_segment_context_still_works(session: Session) -> None:
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
    album_id = _persist(session, album)

    result = caption(album_id, session, FakeThumbnails(), _valid_result_model())

    assert result.captioned == 1
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.caption


# --- Order independence: caption before enhance -----------------------------


def test_caption_running_before_enhance_triggers_shared_call(session: Session) -> None:
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True),
    ]
    album_id = _persist(session, album)

    result = caption(album_id, session, FakeThumbnails({"a1": b"img"}), _valid_result_model())

    assert result.captioned == 1
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.caption == "A generic caption."
    # The tone-delta columns enhance.py owns are populated too, since it's the
    # same underlying analysis call/row write via ensure_analysis.
    assert photo.enhance_exposure_ev == 0.0


def test_caption_after_enhance_reads_back_without_second_call(session: Session) -> None:
    from album.stages.enhance import ensure_analysis

    album = make_album(slug="test-album")
    album.photos = [
        make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True),
    ]
    album_id = _persist(session, album)
    photo = session.scalars(sa_select(Photo)).one()

    ensure_analysis(
        session, photo, thumbnails=FakeThumbnails({"a1": b"img"}), model=_valid_result_model()
    )
    session.flush()

    model, calls = _counting_model()
    result = caption(album_id, session, FakeThumbnails({"a1": b"img"}), model)

    assert calls == []
    assert result.skipped == 1
    assert result.captioned == 0


# --- Only selected photos ----------------------------------------------------


def test_caption_skips_unselected_photos(session: Session) -> None:
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=False),
    ]
    album_id = _persist(session, album)

    result = caption(album_id, session, FakeThumbnails(), _valid_result_model())

    assert result.captioned == 0
    assert result.skipped == 0
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.caption is None


# --- Per-photo failure isolation --------------------------------------------


def test_caption_isolates_per_photo_failure(session: Session) -> None:
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(album, immich_asset_id="good", filename="good.jpg", selected=True),
        make_photo(album, immich_asset_id="bad", filename="bad.jpg", selected=True),
    ]
    album_id = _persist(session, album)

    class RaisingThumbnails:
        def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
            if asset_id == "bad":
                raise RuntimeError("boom")
            return b"img"

    result = caption(album_id, session, RaisingThumbnails(), _valid_result_model())

    assert result.captioned == 1
    assert result.failed == 1
    photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
    assert photos["good"].caption
    assert photos["bad"].caption is None


# --- Scoping and unknown album -----------------------------------------------


def test_caption_scoped_to_album(session: Session) -> None:
    other_album = make_album(slug="other")
    other_album.photos = [
        make_photo(other_album, immich_asset_id="o1", filename="o1.jpg", selected=True)
    ]
    other_id = _persist(session, other_album)
    target_album = make_album(slug="target")
    target_album.photos = [
        make_photo(target_album, immich_asset_id="t1", filename="t1.jpg", selected=True)
    ]
    target_id = _persist(session, target_album)

    caption(target_id, session, FakeThumbnails(), _valid_result_model())

    other_photo = session.scalars(sa_select(Photo).where(Photo.album_id == other_id)).one()
    assert other_photo.caption is None
    target_photo = session.scalars(sa_select(Photo).where(Photo.album_id == target_id)).one()
    assert target_photo.caption is not None


def test_caption_raises_for_unknown_album(session: Session) -> None:
    with pytest.raises(ValueError):
        caption(999, session, FakeThumbnails(), _valid_result_model())


def test_caption_force_reanalyzes_already_captioned_photo(session: Session) -> None:
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True),
    ]
    album_id = _persist(session, album)
    photo = session.scalars(sa_select(Photo)).one()
    photo.enhance_exposure_ev = 0.1
    photo.enhance_temp_shift_k = 50.0
    photo.enhance_shadows = 0.0
    photo.enhance_highlights = 0.0
    photo.caption = "Old caption."
    session.flush()

    result = caption(
        album_id, session, FakeThumbnails({"a1": b"img"}), _valid_result_model(), force=True
    )

    assert result.captioned == 1
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.caption == "A generic caption."
