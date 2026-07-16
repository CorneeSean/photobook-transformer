"""Tests for :class:`~album.photo_analyzer.PhotoAnalyzer` — the shared
"one combined vision call" trigger.

``ensure`` is the single point that runs (and persists) the vision call for a
photo, exactly once; both ``EnhanceStage`` and ``CaptionStage`` route through it.
Its no-double-call contract is tested explicitly with PydanticAI's
``TestModel``/``FunctionModel`` (build-order step 10).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Person, Photo
from album.photo_analyzer import PhotoAnalyzer
from tests.doubles import (
    FakeThumbnails,
    analysis_test_model,
    canned_analysis_response,
    counting_analysis_model,
)


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


class TestPeopleGrounding:
    """Step 19: a photo's *named* people are resolved (via the album-scoped
    ``Person`` cache) and passed into the shared analysis call's grounding
    context, so captions can name subjects — opt-in, and a face-free photo is
    unchanged."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    @pytest.fixture
    def capturing_model(self) -> tuple[FunctionModel, list[str]]:
        """A :class:`FunctionModel` plus a list capturing the text of each user
        prompt it receives, so a test can assert what grounding ``ensure`` passed
        into the shared analysis call. Local to this class — it's the only one
        that inspects the prompt (quality-practices.md §2.4)."""
        prompts: list[str] = []

        def handler(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
            for message in messages:
                for part in message.parts:
                    # ``analyze_photo`` always sends the prompt as a list
                    # (``[format_context(...), BinaryContent(...)]``), so the user
                    # part's content is a sequence; pull out its text element(s).
                    if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                        prompts.extend(item for item in part.content if isinstance(item, str))
            return canned_analysis_response()

        return FunctionModel(handler), prompts

    def test_named_people_reach_the_prompt(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
        capturing_model: tuple[FunctionModel, list[str]],
    ) -> None:
        album = make_album()
        album.people.append(Person(immich_person_id="p-alex", name="Alex"))
        album.people.append(Person(immich_person_id="p-sam", name="Sam"))
        album.photos = [make_photo(album, immich_asset_id="a1", person_ids=["p-alex", "p-sam"])]
        self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_model
        PhotoAnalyzer(FakeThumbnails(), model=model).ensure(session, photo)

        joined = "\n".join(prompts)
        assert "Alex" in joined
        assert "Sam" in joined

    def test_unnamed_and_unknown_people_are_dropped(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
        capturing_model: tuple[FunctionModel, list[str]],
    ) -> None:
        # A recognized-but-unnamed person (Person.name is None) and a person_id
        # with no cached Person row both contribute no name — and no "None" or raw
        # id leaks into the prompt.
        album = make_album()
        album.people.append(Person(immich_person_id="p-alex", name="Alex"))
        album.people.append(Person(immich_person_id="p-unnamed", name=None))
        album.photos = [
            make_photo(
                album,
                immich_asset_id="a1",
                person_ids=["p-alex", "p-unnamed", "p-ghost"],
            )
        ]
        self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_model
        PhotoAnalyzer(FakeThumbnails(), model=model).ensure(session, photo)

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
        capturing_model: tuple[FunctionModel, list[str]],
    ) -> None:
        # Genericity guard: person_ids unset (never synced / no faces) ⇒ no
        # people line at all, so the prompt matches the pre-step-19 behavior.
        album = make_album()
        album.photos = [make_photo(album, immich_asset_id="a1", person_ids=None)]
        self._persist(session, album)
        photo = session.scalars(sa_select(Photo)).one()

        model, prompts = capturing_model
        PhotoAnalyzer(FakeThumbnails(), model=model).ensure(session, photo)

        assert "People in this photo" not in "\n".join(prompts)
