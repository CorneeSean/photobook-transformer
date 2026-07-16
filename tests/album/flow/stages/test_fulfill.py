"""Tests for :class:`~album.flow.stages.fulfill.FulfillStage` over in-memory SQLite.

``fulfill`` is medium-agnostic: it delegates to an injected
:class:`~album.printers.base.Printer` and records the reference the printer
returns. A fake printer keeps these tests pure-logic and fast
(working-agreement.md); the real printers are covered in test_html_printer.py
and test_lulu_printer.py, and the Lulu HTTP layer in test_lulu_client.py.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from album.db.models import Album
from album.flow.stages.fulfill import FulfillStage
from album.printers.base import PrintResult


class FakePrinter:
    """An in-memory :class:`~album.printers.base.Printer` for tests."""

    def __init__(self, output_ref: str = "book-ref-1") -> None:
        self._output_ref = output_ref
        self.calls: list[int] = []

    def print(self, album: Album, session: Session) -> PrintResult:
        assert album.id is not None
        self.calls.append(album.id)
        return PrintResult(output_ref=self._output_ref)


class TestFulfillStage:
    """`FulfillStage.run`: delegates to the printer, stores the ref, idempotent."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def test_prints_and_stores_output_ref(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album())
        printer = FakePrinter(output_ref="data/albums/honeymoon-2026/book/index.html")

        result = FulfillStage(printer).run(album_id, session)

        assert result.printed is True
        assert result.output_ref == "data/albums/honeymoon-2026/book/index.html"
        assert printer.calls == [album_id]
        album = session.get(Album, album_id)
        assert album is not None
        assert album.output_ref == "data/albums/honeymoon-2026/book/index.html"

    def test_idempotent_skips_when_already_printed(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(output_ref="already-printed"))
        printer = FakePrinter(output_ref="should-not-be-used")

        result = FulfillStage(printer).run(album_id, session)

        assert result.printed is False
        assert result.output_ref == "already-printed"
        assert printer.calls == []
        album = session.get(Album, album_id)
        assert album is not None
        assert album.output_ref == "already-printed"

    def test_force_reprints_even_if_already_printed(
        self, session: Session, make_album: Callable[..., Album]
    ) -> None:
        album_id = self._persist(session, make_album(output_ref="old-ref"))
        printer = FakePrinter(output_ref="new-ref")

        result = FulfillStage(printer).run(album_id, session, force=True)

        assert result.printed is True
        assert result.output_ref == "new-ref"
        assert printer.calls == [album_id]
        album = session.get(Album, album_id)
        assert album is not None
        assert album.output_ref == "new-ref"

    def test_raises_on_unknown_album(self, session: Session) -> None:
        printer = FakePrinter()

        with pytest.raises(ValueError, match="no album"):
            FulfillStage(printer).run(999, session)

        assert printer.calls == []
