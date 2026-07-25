"""Shared object builders for tests.

These are the concrete builders behind the ``make_album`` / ``make_segment`` /
``make_photo`` **factory fixtures** in ``conftest.py`` (quality-practices.md
§2.3): a test asks for the fixture it needs and gets one of these callables with
sensible defaults. Kept in their own module (rather than inline in each fixture)
so the fixtures stay thin and the defaults live in exactly one place.
"""

from __future__ import annotations

from datetime import UTC, datetime

from album.db.models import Album, Photo, Segment


def make_album(**overrides: object) -> Album:
    defaults: dict[str, object] = {
        "slug": "honeymoon-2026",
        "title": "Our Honeymoon",
    }
    defaults.update(overrides)
    return Album(**defaults)  # pyright: ignore[reportArgumentType]


def make_segment(album: Album, **overrides: object) -> Segment:
    defaults: dict[str, object] = {
        "album": album,
        "position": 1,
        "label": "Day 1",
        "start_at": datetime(2026, 1, 1, tzinfo=UTC),
        "end_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Segment(**defaults)  # pyright: ignore[reportArgumentType]


def make_photo(album: Album, **overrides: object) -> Photo:
    defaults: dict[str, object] = {
        "album": album,
        "immich_asset_id": "asset-1",
        "filename": "IMG_0001.jpg",
    }
    defaults.update(overrides)
    return Photo(**defaults)  # pyright: ignore[reportArgumentType]
