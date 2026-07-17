"""Tests for :class:`~album.analysis.tone.ToneParams` — the tone value object and
its conversion to/from the ``Photo`` row.

The behaviour worth pinning directly (rather than only via ``enhance``/
``photo_analyzer``) is: ``from_photo`` coalesces an unset ``enhance_*`` column to
``0.0``, and ``write_to`` writes the four ``enhance_*`` columns while leaving
``caption`` untouched.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from album.analysis.tone import ToneParams
from album.db.models import Album, Photo


class TestFromPhoto:
    """`Photo -> ToneParams`: read the stored knobs, coalescing None to 0.0."""

    def test_reads_stored_values(
        self,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        photo = make_photo(
            make_album(),
            enhance_exposure_ev=0.4,
            enhance_temp_shift_k=-200.0,
            enhance_shadows=0.3,
            enhance_highlights=-0.2,
        )

        assert ToneParams.from_photo(photo) == ToneParams(
            exposure_ev=0.4,
            temp_shift_k=-200.0,
            shadows=0.3,
            highlights=-0.2,
        )

    @pytest.mark.parametrize(
        "column",
        [
            "enhance_exposure_ev",
            "enhance_temp_shift_k",
            "enhance_shadows",
            "enhance_highlights",
        ],
    )
    def test_unset_column_coalesces_to_zero(
        self,
        column: str,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # Every enhance_* column defaults to None on a fresh Photo; the one left
        # unset must read back as 0.0, not raise or leak None into the arithmetic.
        set_columns = {
            "enhance_exposure_ev": 1.0,
            "enhance_temp_shift_k": 1.0,
            "enhance_shadows": 1.0,
            "enhance_highlights": 1.0,
        }
        del set_columns[column]
        photo = make_photo(make_album(), **set_columns)

        tone = ToneParams.from_photo(photo)

        assert getattr(tone, column.removeprefix("enhance_")) == 0.0


class TestWriteTo:
    """`ToneParams -> Photo`: write the four columns, never touch caption."""

    def test_writes_columns_and_leaves_caption_untouched(
        self,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        photo = make_photo(make_album(), caption="pre-existing caption")
        tone = ToneParams(exposure_ev=0.5, temp_shift_k=150.0, shadows=0.2, highlights=-0.3)

        tone.write_to(photo)

        assert photo.enhance_exposure_ev == 0.5
        assert photo.enhance_temp_shift_k == 150.0
        assert photo.enhance_shadows == 0.2
        assert photo.enhance_highlights == -0.3
        assert photo.caption == "pre-existing caption"
