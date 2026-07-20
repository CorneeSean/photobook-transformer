"""Tests for :class:`~album.flow.stages.assemble.AssembleStage` over in-memory SQLite.

Covers page count and photo placement given fixture data, **including a
segment-less album** (the genericity fallback for this stage — no section pages,
one implicit group). ``assemble`` now produces only the layout (each placed
photo's ``page_number`` + the page count); the concrete master PDF is rendered
on demand by :class:`~album.printers.lulu.LuluPrinter` and is covered in
test_lulu_printer.py.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo, Segment
from album.db.session import reuse
from album.flow.stages.assemble import AssembleResult, AssembleStage, spine_width_in


def _dt(hour: int, minute: int = 0, day: int = 2) -> datetime:
    return datetime(2026, 5, day, hour, minute, tzinfo=UTC)


class TestAssembleStage:
    """`AssembleStage.run`: placement, section pages, idempotency."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    @pytest.fixture
    def selected_photo(self, make_photo: Callable[..., Photo]) -> Callable[..., Photo]:
        """Builder for a placed-ready photo: selected, rendered, timestamped, captioned."""

        def _make(album: Album, asset: str, **overrides: object) -> Photo:
            defaults: dict[str, object] = {
                "selected": True,
                "rendered_path": "tests/fixtures/images/sharp.jpg",
                "timestamp": _dt(12),
                "caption": f"caption for {asset}",
            }
            defaults.update(overrides)
            return make_photo(album, immich_asset_id=asset, filename=f"{asset}.jpg", **defaults)

        return _make

    def test_raises_for_unknown_album(self, session: Session) -> None:
        with pytest.raises(ValueError, match="no album"):
            AssembleStage(999, reuse(session)).run()

    def test_segment_less_album_assembles_with_one_implicit_group(
        self,
        session: Session,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        """Genericity guard: zero segments must still lay out correctly, with no
        section-header pages."""
        album = make_album()
        album.photos = [
            selected_photo(album, "a1", scene_id=1, timestamp=_dt(9)),
            selected_photo(album, "a2", scene_id=1, timestamp=_dt(9, 5)),
            selected_photo(album, "a3", scene_id=1, timestamp=_dt(9, 10)),
            selected_photo(album, "a4", scene_id=2, timestamp=_dt(14)),
            # not selected -> must be excluded from placement
            selected_photo(album, "a5", scene_id=2, timestamp=_dt(14, 5), selected=False),
        ]
        album_id = self._persist(session, album)

        result = AssembleStage(album_id, reuse(session)).run()

        assert isinstance(result, AssembleResult)
        assert result.photos_placed == 4
        assert not result.skipped

        # 3 photos in scene 1 -> one 3-up page; 1 photo in scene 2 -> one page.
        # No section pages: 2 total.
        assert result.pages == 2

        placed = {
            p.immich_asset_id: p.page_number
            for p in session.scalars(sa_select(Photo).where(Photo.album_id == album_id))
        }
        assert placed["a1"] == placed["a2"] == placed["a3"] == 1
        assert placed["a4"] == 2
        assert placed["a5"] is None  # unselected photo never placed

    def test_segments_produce_section_header_pages_in_position_order(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_segment: Callable[..., Segment],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        day1 = make_segment(
            album,
            position=1,
            label="Day 1",
            place="Lisbon",
            start_at=_dt(0),
            end_at=_dt(23, 59),
        )
        day2 = make_segment(
            album,
            position=2,
            label="Day 2",
            place="Porto",
            start_at=_dt(0, day=3),
            end_at=_dt(23, 59, day=3),
        )
        album.segments = [day1, day2]
        album.photos = [
            selected_photo(album, "b1", segment=day2, scene_id=2, timestamp=_dt(10, day=3)),
            selected_photo(album, "b2", segment=day1, scene_id=1, timestamp=_dt(9)),
        ]
        album_id = self._persist(session, album)

        result = AssembleStage(album_id, reuse(session)).run()

        # section(day1) + photo page(day1) + section(day2) + photo page(day2) = 4
        assert result.pages == 4
        assert result.photos_placed == 2

        placed = {
            p.immich_asset_id: p.page_number
            for p in session.scalars(sa_select(Photo).where(Photo.album_id == album_id))
        }
        # day1's section is page 1, its photo page 2; day2's section page 3, photo page 4.
        assert placed["b2"] == 2
        assert placed["b1"] == 4

    def test_photo_outside_any_segment_still_placed_in_fallback_group(
        self,
        session: Session,
        make_album: Callable[..., Album],
        make_segment: Callable[..., Segment],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        day1 = make_segment(album, position=1, label="Day 1", start_at=_dt(0), end_at=_dt(23, 59))
        album.segments = [day1]
        album.photos = [
            selected_photo(album, "c1", segment=day1, scene_id=1, timestamp=_dt(9)),
            # segment_id None despite the album having segments (out-of-range photo,
            # per cluster.py's ClusterResult.out_of_range) -> falls into its own group.
            selected_photo(album, "c2", segment_id=None, scene_id=None, timestamp=_dt(9, day=10)),
        ]
        album_id = self._persist(session, album)

        result = AssembleStage(album_id, reuse(session)).run()

        assert result.photos_placed == 2
        placed = {
            p.immich_asset_id: p.page_number
            for p in session.scalars(sa_select(Photo).where(Photo.album_id == album_id))
        }
        assert placed["c1"] is not None
        assert placed["c2"] is not None

    def test_idempotent_skips_when_already_assembled(
        self,
        session: Session,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [selected_photo(album, "d1", scene_id=1)]
        album_id = self._persist(session, album)

        first = AssembleStage(album_id, reuse(session)).run()
        assert not first.skipped
        assert first.photos_placed == 1

        second = AssembleStage(album_id, reuse(session)).run()
        assert second.skipped
        assert second.pages == first.pages
        assert second.photos_placed == first.photos_placed

    def test_force_regenerates(
        self,
        session: Session,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [selected_photo(album, "e1", scene_id=1)]
        album_id = self._persist(session, album)

        first = AssembleStage(album_id, reuse(session)).run()
        second = AssembleStage(album_id, reuse(session), force=True).run()

        assert not second.skipped
        assert second.pages == first.pages

    def test_force_clears_stale_page_number_for_deselected_photo(
        self,
        session: Session,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        """A photo placed by an earlier run that's since been deselected must not
        keep a stale page_number after a forced re-assemble."""
        album = make_album()
        photo = selected_photo(album, "e2", scene_id=1)
        album.photos = [photo]
        album_id = self._persist(session, album)

        AssembleStage(album_id, reuse(session)).run()
        assert photo.page_number is not None

        photo.selected = False
        session.flush()

        AssembleStage(album_id, reuse(session), force=True).run()

        assert photo.page_number is None

    def test_no_selected_photos_places_nothing(
        self,
        session: Session,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [selected_photo(album, "g1", scene_id=1, selected=False)]
        album_id = self._persist(session, album)

        result = AssembleStage(album_id, reuse(session)).run()

        assert result.pages == 0
        assert result.photos_placed == 0
        assert not result.skipped

    def test_raises_when_a_selected_photo_has_no_rendered_path(
        self,
        session: Session,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        """`render` (step 11) must populate `rendered_path` before `assemble` runs;
        a selected photo without one is a pipeline-ordering error, not something
        to silently paper over with a broken `<img src="None">`."""
        album = make_album()
        album.photos = [selected_photo(album, "j1", scene_id=1, rendered_path=None)]
        album_id = self._persist(session, album)

        with pytest.raises(ValueError, match="rendered_path"):
            AssembleStage(album_id, reuse(session)).run()

    def test_page_count_reflects_layout_capacity_cycle(
        self,
        session: Session,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        """7 photos in one scene cycle as 3 + 2 + 1 + 1 -> 4 pages."""
        album = make_album()
        album.photos = [
            selected_photo(album, f"i{n}", scene_id=1, timestamp=_dt(9, n)) for n in range(7)
        ]
        album_id = self._persist(session, album)

        result = AssembleStage(album_id, reuse(session)).run()

        assert result.photos_placed == 7
        assert result.pages == 4


class TestSpineWidth:
    """The Lulu softcover perfect-bound spine-width formula (a pure module fn)."""

    def test_formula(self) -> None:
        # page_count / 444 + 0.06in (open question 4).
        assert spine_width_in(0) == pytest.approx(0.06)
        assert spine_width_in(444) == pytest.approx(1.06)
        assert spine_width_in(222) == pytest.approx(0.56)
