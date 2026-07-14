"""Tests for :class:`~album.printers.html.HtmlPrinter` over in-memory SQLite.

The printer renders the assembled book (photos placed by ``assemble``, i.e.
``selected`` with a ``page_number``) to a single static ``index.html`` whose
``<img>`` tags point at the on-disk rendered files by relative path. These tests
assert the page grouping, captions, segment dividers, the genericity fallback for
a segment-less album, and the run-assemble-first guard — without a browser (the
markup is plain text we can inspect).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from album.printers.base import NotAssembledError
from album.printers.html import HtmlPrinter

from .conftest import make_album, make_photo, make_segment


def _rendered_file(tmp_path: Path, name: str) -> str:
    """Create a stand-in rendered image on disk and return its absolute path."""
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    path = media / name
    _ = path.write_bytes(b"\xff\xd8\xff")  # not a real JPEG; the printer only references it
    return str(path)


def test_writes_page_by_page_book_with_captions_and_segment(
    session: Session, tmp_path: Path
) -> None:
    album = make_album(slug="honeymoon-2026", title="Our Honeymoon")
    segment = make_segment(album, label="Day 4", place="Lisbon")
    img1 = _rendered_file(tmp_path, "img1.jpg")
    img2 = _rendered_file(tmp_path, "img2.jpg")
    img3 = _rendered_file(tmp_path, "img3.jpg")
    # page 1: two photos; page 2: one photo. All under one segment.
    make_photo(
        album,
        immich_asset_id="a1",
        selected=True,
        page_number=1,
        segment=segment,
        rendered_path=img1,
        caption="Sunrise over the bay",
    )
    make_photo(
        album,
        immich_asset_id="a2",
        selected=True,
        page_number=1,
        segment=segment,
        rendered_path=img2,
        caption="Coffee & maps",
    )  # ampersand → must be escaped
    make_photo(
        album,
        immich_asset_id="a3",
        selected=True,
        page_number=2,
        segment=segment,
        rendered_path=img3,
        caption="The old town gate",
    )
    # excluded: not selected, and selected-but-unplaced.
    make_photo(album, immich_asset_id="a4", selected=False, rendered_path=img1)
    make_photo(album, immich_asset_id="a5", selected=True, page_number=None, rendered_path=img1)
    session.add(album)
    session.commit()

    result = HtmlPrinter(output_dir=tmp_path).print(album, session)

    index_path = tmp_path / "honeymoon-2026" / "book" / "index.html"
    assert result.output_ref == str(index_path)
    assert result.detail == {"pages": 2}
    assert index_path.exists()
    html = index_path.read_text(encoding="utf-8")

    assert "Our Honeymoon" in html
    assert "Sunrise over the bay" in html
    assert "The old town gate" in html
    assert "Coffee &amp; maps" in html  # autoescaped
    assert "Day 4" in html and "Lisbon" in html  # segment divider

    book_dir = index_path.parent
    for img in (img1, img2, img3):
        rel = Path(os.path.relpath(img, start=book_dir)).as_posix()
        assert f'<img src="{rel}"' in html
        assert (book_dir / rel).resolve() == Path(img).resolve()  # loads from disk

    # excluded photos are not on any page: img1 appears only via a1 (page 1),
    # the unselected/unplaced dupes must not add a second <img>.
    assert html.count("<img src=") == 3


def test_album_without_segments_renders(session: Session, tmp_path: Path) -> None:
    """Genericity fallback: an album with zero segments still produces a book,
    with no segment divider and no crash."""
    album = make_album(slug="no-segments")
    make_photo(
        album,
        immich_asset_id="a1",
        selected=True,
        page_number=1,
        rendered_path=_rendered_file(tmp_path, "x.jpg"),
        caption="just a photo",
    )
    session.add(album)
    session.commit()

    result = HtmlPrinter(output_dir=tmp_path).print(album, session)

    assert result.detail == {"pages": 1}
    html = (tmp_path / "no-segments" / "book" / "index.html").read_text(encoding="utf-8")
    assert "just a photo" in html
    assert "segment-ribbon" not in html  # no divider without a segment


def test_multi_segment_draws_one_divider_per_segment_in_order(
    session: Session, tmp_path: Path
) -> None:
    """The segment divider fires exactly once per segment — on the first page of
    each — and segments appear in page order. Guards ``iter_book_pages``'
    ``starts_segment`` dedup, which single-segment tests can't exercise."""
    album = make_album(slug="multi")
    day1 = make_segment(album, position=1, label="Day 1", place="Lisbon")
    day2 = make_segment(album, position=2, label="Day 2", place="Perast")
    # Day 1 spans two pages (so the divider must NOT repeat on its page 2),
    # then Day 2 starts on page 3.
    specs = [
        (1, day1, "d1p1"),
        (1, day1, "d1p1b"),
        (2, day1, "d1p2"),
        (3, day2, "d2p3"),
        (4, day2, "d2p4"),
    ]
    for i, (page, segment, name) in enumerate(specs):
        make_photo(
            album,
            immich_asset_id=f"a{i}",
            selected=True,
            page_number=page,
            segment=segment,
            rendered_path=_rendered_file(tmp_path, f"{name}.jpg"),
            caption=name,
        )
    session.add(album)
    session.commit()

    result = HtmlPrinter(output_dir=tmp_path).print(album, session)

    assert result.detail == {"pages": 4}
    html = (tmp_path / "multi" / "book" / "index.html").read_text(encoding="utf-8")
    # One divider per segment (not per page): two segments → two ribbons.
    assert html.count("segment-ribbon") == 2
    # ...and in page order: Day 1's ribbon precedes Day 2's.
    assert html.index("Day 1") < html.index("Day 2")


def test_raises_when_nothing_assembled(session: Session, tmp_path: Path) -> None:
    album = make_album(slug="empty")
    # selected but not placed (assemble hasn't run) → nothing to print.
    make_photo(
        album,
        immich_asset_id="a1",
        selected=True,
        page_number=None,
        rendered_path=_rendered_file(tmp_path, "x.jpg"),
    )
    session.add(album)
    session.commit()

    with pytest.raises(NotAssembledError, match="assemble"):
        HtmlPrinter(output_dir=tmp_path).print(album, session)
