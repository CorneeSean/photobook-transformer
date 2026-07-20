"""Tests for :class:`~album.printers.lulu.LuluPrinter` — which renders the master
PDF on demand from the assembled layout and submits it to Lulu's Print API.

Runs against a fake ``LuluClient``-shaped object rather than real HTTP (that
exchange is covered in test_lulu_client.py). Structural PDF assertions (page
count, page-box dimensions matching the resolved Lulu trim+bleed spec) stand in
for the build-order's "manually verify the PDF opens with correct trim/bleed"
line — see knowledge/process/working-agreement.md's per-stage testing table.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pypdf
import pytest
from sqlalchemy.orm import Session

from album.db.models import Album, Photo, Segment
from album.lulu.client import PrintJob, PrintJobStatus, ShippingAddress
from album.printers.base import NotAssembledError
from album.printers.lulu import (
    PAGE_HEIGHT_IN,
    PAGE_WIDTH_IN,
    LuluPrinter,
    UnreachablePdfError,
)

_FIXTURE_IMAGE = str(
    Path(__file__).parent.parent.parent / "fixtures" / "images" / "sharp.jpg",
)

# PDF points per inch (a "PDF point" is always 1/72in — a unit conversion for the
# assertion, not a Lulu-specific constant).
_POINTS_PER_IN = 72.0


def _dt(hour: int, day: int = 2) -> datetime:
    return datetime(2026, 5, day, hour, tzinfo=UTC)


class FakeLuluClient:
    """A fake shaped like :class:`~album.lulu.client.LuluClient` for printer tests."""

    def __init__(self, order_id: int = 555) -> None:
        self._order_id = order_id
        self.requests: list[object] = []

    def submit_print_job(self, request: object) -> PrintJob:
        self.requests.append(request)
        return PrintJob(id=self._order_id, status=PrintJobStatus(name="CREATED"))


class TestLuluPrinter:
    """`LuluPrinter.print`: renders the PDF, guards reachability, submits."""

    def _shipping_address(self) -> ShippingAddress:
        return ShippingAddress(
            name="Alex Morgan",
            street1="1 Main St",
            city="Springfield",
            postcode="00000",
            country_code="US",
            phone_number="+15555555555",
        )

    def _printer(
        self,
        client: FakeLuluClient,
        output_dir: Path,
        *,
        publish_pdf: Callable[[Path], str] | None = None,
    ) -> LuluPrinter:
        return LuluPrinter(
            client,  # pyright: ignore[reportArgumentType]
            contact_email="orders@example.com",
            pod_package_id="0850X0850FCPRECW060UW444MXX",
            shipping_address=self._shipping_address(),
            output_dir=output_dir,
            publish_pdf=publish_pdf,
        )

    @pytest.fixture
    def placed_photo(self, make_photo: Callable[..., Photo]) -> Callable[..., Photo]:
        """Builder for a laid-out photo: selected, rendered, and page-stamped."""

        def _make(album: Album, asset: str, page_number: int, **overrides: object) -> Photo:
            defaults: dict[str, object] = {
                "selected": True,
                "rendered_path": _FIXTURE_IMAGE,
                "timestamp": _dt(12),
                "caption": f"caption for {asset}",
                "page_number": page_number,
            }
            defaults.update(overrides)
            return make_photo(album, immich_asset_id=asset, filename=f"{asset}.jpg", **defaults)

        return _make

    def _persist(self, session: Session, album: Album) -> None:
        session.add(album)
        session.commit()

    def test_raises_when_not_assembled_yet(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
    ) -> None:
        printer = self._printer(FakeLuluClient(), tmp_path)
        album = make_album()  # no placed photos
        self._persist(session, album)

        with pytest.raises(NotAssembledError, match="placed"):
            printer.print(album, session)

    def test_renders_pdf_then_refuses_without_a_publisher(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        placed_photo: Callable[..., Photo],
    ) -> None:
        """The known gap: with no ``publish_pdf`` hook the PDF is still rendered to
        disk (and recorded), but submission is refused since Lulu can't fetch a
        local file."""
        printer = self._printer(FakeLuluClient(), tmp_path)
        album = make_album(slug="honeymoon-2026")
        album.photos = [placed_photo(album, "a1", page_number=1, scene_id=1)]
        self._persist(session, album)

        with pytest.raises(UnreachablePdfError):
            printer.print(album, session)

        pdf_path = tmp_path / "honeymoon-2026" / "assembled.pdf"
        assert pdf_path.exists()
        assert album.assembled_pdf_path == str(pdf_path)

    def test_submits_when_publisher_hosts_the_pdf(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        placed_photo: Callable[..., Photo],
    ) -> None:
        fake_client = FakeLuluClient(order_id=777)

        def publish(path: Path) -> str:
            return f"https://example.com/{path.name}"

        printer = self._printer(fake_client, tmp_path, publish_pdf=publish)
        album = make_album(slug="honeymoon-2026")
        album.photos = [placed_photo(album, "a1", page_number=1, scene_id=1)]
        self._persist(session, album)

        result = printer.print(album, session)

        assert result.output_ref == "777"
        assert len(fake_client.requests) == 1

    def test_rejects_a_non_http_published_url(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        placed_photo: Callable[..., Photo],
    ) -> None:
        printer = self._printer(
            FakeLuluClient(),
            tmp_path,
            publish_pdf=lambda path: str(path),  # returns a local path, not a URL
        )
        album = make_album(slug="honeymoon-2026")
        album.photos = [placed_photo(album, "a1", page_number=1, scene_id=1)]
        self._persist(session, album)

        with pytest.raises(UnreachablePdfError, match="not an http"):
            printer.print(album, session)

    def test_pdf_page_box_matches_trim_plus_bleed(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        placed_photo: Callable[..., Photo],
    ) -> None:
        printer = self._printer(
            FakeLuluClient(),
            tmp_path,
            publish_pdf=lambda path: f"https://example.com/{path.name}",
        )
        album = make_album(slug="honeymoon-2026")
        album.photos = [placed_photo(album, "a1", page_number=1, scene_id=1)]
        self._persist(session, album)

        printer.print(album, session)

        reader = pypdf.PdfReader(str(tmp_path / "honeymoon-2026" / "assembled.pdf"))
        page = reader.pages[0]
        width_in = float(page.mediabox.width) / _POINTS_PER_IN
        height_in = float(page.mediabox.height) / _POINTS_PER_IN
        assert width_in == pytest.approx(PAGE_WIDTH_IN, abs=0.02)
        assert height_in == pytest.approx(PAGE_HEIGHT_IN, abs=0.02)

    def test_segment_first_page_renders_a_section_page(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_segment: Callable[..., Segment],
        placed_photo: Callable[..., Photo],
    ) -> None:
        """A segment's first page gets a dedicated section-header page in the PDF,
        reconstructed from ``iter_book_pages``'s ``starts_segment`` — so one
        segment + one photo page renders as two PDF pages."""
        printer = self._printer(
            FakeLuluClient(),
            tmp_path,
            publish_pdf=lambda path: f"https://example.com/{path.name}",
        )
        album = make_album(slug="honeymoon-2026")
        day1 = make_segment(album, position=1, label="Day 1", place="Lisbon")
        album.segments = [day1]
        # page 1 is assemble's section page (no photos); the photo lands on page 2.
        album.photos = [placed_photo(album, "a1", page_number=2, scene_id=1, segment=day1)]
        self._persist(session, album)

        printer.print(album, session)

        reader = pypdf.PdfReader(str(tmp_path / "honeymoon-2026" / "assembled.pdf"))
        assert len(reader.pages) == 2
