"""Tests for :class:`~album.stages.assemble.AssembleStage` over in-memory SQLite.

Covers page count and photo placement given fixture data, **including a
segment-less album** (the genericity fallback for this stage — no section pages,
one implicit group). Structural PDF assertions (page count, page-box dimensions
matching the resolved Lulu trim+bleed spec) stand in for the build-order's
"manually verify the PDF opens with correct trim/bleed" line — see
knowledge/working-agreement.md's per-stage testing table.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pypdf
import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo, Segment
from album.stages.assemble import (
    BLEED_IN,
    PAGE_HEIGHT_IN,
    PAGE_WIDTH_IN,
    TRIM_HEIGHT_IN,
    TRIM_WIDTH_IN,
    AssembleResult,
    AssembleStage,
    spine_width_in,
)

_FIXTURE_IMAGE = str(Path(__file__).parent.parent.parent / "fixtures" / "images" / "sharp.jpg")

# PDF points per inch (a "PDF point" is always 1/72in — this is a unit
# conversion for the assertion, not a Lulu-specific constant).
_POINTS_PER_IN = 72.0


def _dt(hour: int, minute: int = 0, day: int = 2) -> datetime:
    return datetime(2026, 5, day, hour, minute, tzinfo=UTC)


class TestAssembleStage:
    """`AssembleStage.run`: placement, section pages, idempotency, PDF structure."""

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
                "rendered_path": _FIXTURE_IMAGE,
                "timestamp": _dt(12),
                "caption": f"caption for {asset}",
            }
            defaults.update(overrides)
            return make_photo(album, immich_asset_id=asset, filename=f"{asset}.jpg", **defaults)

        return _make

    def test_raises_for_unknown_album(self, session: Session) -> None:
        with pytest.raises(ValueError, match="no album"):
            AssembleStage("unused").run(999, session)

    def test_segment_less_album_assembles_with_one_implicit_group(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        """Genericity guard: zero segments must still assemble correctly, with no
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

        result = AssembleStage(tmp_path).run(album_id, session)

        assert isinstance(result, AssembleResult)
        assert result.photos_placed == 4
        assert not result.skipped
        assert result.output_path is not None
        assert Path(result.output_path).exists()

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

        reader = pypdf.PdfReader(result.output_path)
        assert len(reader.pages) == 2

    def test_segments_produce_section_header_pages_in_position_order(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_segment: Callable[..., Segment],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        day1 = make_segment(
            album, position=1, label="Day 1", place="Lisbon", start_at=_dt(0), end_at=_dt(23, 59)
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

        result = AssembleStage(tmp_path).run(album_id, session)

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
        tmp_path: Path,
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

        result = AssembleStage(tmp_path).run(album_id, session)

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
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [selected_photo(album, "d1", scene_id=1)]
        album_id = self._persist(session, album)
        stage = AssembleStage(tmp_path)

        first = stage.run(album_id, session)
        assert not first.skipped

        second = stage.run(album_id, session)
        assert second.skipped
        assert second.output_path == first.output_path

    def test_force_regenerates(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [selected_photo(album, "e1", scene_id=1)]
        album_id = self._persist(session, album)
        stage = AssembleStage(tmp_path)

        first = stage.run(album_id, session)
        second = stage.run(album_id, session, force=True)

        assert not second.skipped
        assert second.output_path == first.output_path

    def test_force_clears_stale_page_number_for_deselected_photo(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        """A photo placed by an earlier run that's since been deselected must not
        keep a stale page_number after a forced re-assemble."""
        album = make_album()
        photo = selected_photo(album, "e2", scene_id=1)
        album.photos = [photo]
        album_id = self._persist(session, album)
        stage = AssembleStage(tmp_path)

        stage.run(album_id, session)
        assert photo.page_number is not None

        photo.selected = False
        session.flush()

        stage.run(album_id, session, force=True)

        assert photo.page_number is None

    def test_output_path_matches_album_slug_and_is_stored_on_album(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="wedding-2026")
        album.photos = [selected_photo(album, "f1", scene_id=1)]
        album_id = self._persist(session, album)

        result = AssembleStage(tmp_path).run(album_id, session)

        assert result.output_path == str(tmp_path / "wedding-2026" / "assembled.pdf")
        stored = session.get(Album, album_id)
        assert stored is not None
        assert stored.assembled_pdf_path == result.output_path

    def test_no_selected_photos_still_produces_a_valid_zero_page_pdf(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [selected_photo(album, "g1", scene_id=1, selected=False)]
        album_id = self._persist(session, album)

        result = AssembleStage(tmp_path).run(album_id, session)

        assert result.pages == 0
        assert result.photos_placed == 0
        assert result.output_path is not None
        assert Path(result.output_path).exists()

        # The degenerate zero-page PDF must still use the Lulu trim+bleed page
        # size, not WeasyPrint's built-in default (e.g. A4).
        reader = pypdf.PdfReader(result.output_path)
        page = reader.pages[0]
        width_in = float(page.mediabox.width) / _POINTS_PER_IN
        height_in = float(page.mediabox.height) / _POINTS_PER_IN
        assert width_in == pytest.approx(PAGE_WIDTH_IN, abs=0.02)
        assert height_in == pytest.approx(PAGE_HEIGHT_IN, abs=0.02)

    def test_raises_when_a_selected_photo_has_no_rendered_path(
        self,
        session: Session,
        tmp_path: Path,
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
            AssembleStage(tmp_path).run(album_id, session)

    def test_photo_with_relative_rendered_path_resolves_against_cwd(
        self,
        session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        """`_render_pdf`'s `base_url` must resolve `Photo.rendered_path` against the
        process's working directory, not the templates directory."""
        repo_root = Path(__file__).parent.parent.parent.parent
        monkeypatch.chdir(repo_root)
        relative_image = "tests/fixtures/images/sharp.jpg"

        album = make_album()
        album.photos = [selected_photo(album, "k1", scene_id=1, rendered_path=relative_image)]
        album_id = self._persist(session, album)

        result = AssembleStage(tmp_path).run(album_id, session)

        assert result.pages == 1
        assert result.output_path is not None
        assert Path(result.output_path).stat().st_size > 0

    def test_pdf_page_box_matches_trim_plus_bleed(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [selected_photo(album, "h1", scene_id=1)]
        album_id = self._persist(session, album)

        result = AssembleStage(tmp_path).run(album_id, session)
        assert result.output_path is not None
        reader = pypdf.PdfReader(result.output_path)

        assert PAGE_WIDTH_IN == pytest.approx(TRIM_WIDTH_IN + 2 * BLEED_IN)
        assert PAGE_HEIGHT_IN == pytest.approx(TRIM_HEIGHT_IN + 2 * BLEED_IN)

        page = reader.pages[0]
        width_in = float(page.mediabox.width) / _POINTS_PER_IN
        height_in = float(page.mediabox.height) / _POINTS_PER_IN
        assert width_in == pytest.approx(PAGE_WIDTH_IN, abs=0.02)
        assert height_in == pytest.approx(PAGE_HEIGHT_IN, abs=0.02)

    def test_pdf_page_count_matches_result(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        selected_photo: Callable[..., Photo],
    ) -> None:
        album = make_album()
        album.photos = [
            selected_photo(album, f"i{n}", scene_id=1, timestamp=_dt(9, n)) for n in range(7)
        ]
        album_id = self._persist(session, album)

        result = AssembleStage(tmp_path).run(album_id, session)
        assert result.output_path is not None
        reader = pypdf.PdfReader(result.output_path)

        assert len(reader.pages) == result.pages


class TestSpineWidth:
    """The Lulu softcover perfect-bound spine-width formula (a pure module fn)."""

    def test_formula(self) -> None:
        # page_count / 444 + 0.06in (open question 4).
        assert spine_width_in(0) == pytest.approx(0.06)
        assert spine_width_in(444) == pytest.approx(1.06)
        assert spine_width_in(222) == pytest.approx(0.56)
