"""Tests for :class:`~album.printers.lulu.LuluPrinter` — the glue between the
``fulfill`` stage and Lulu's Print API — against a fake ``LuluClient``-shaped
object rather than real HTTP (that exchange is covered in test_lulu_client.py).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from album.lulu.client import PrintJob, PrintJobStatus, ShippingAddress
from album.printers.base import NotAssembledError
from album.printers.lulu import LuluPrinter, UnreachablePdfError

from .conftest import make_album


class FakeLuluClient:
    """A fake shaped like :class:`~album.lulu.client.LuluClient` for printer tests."""

    def __init__(self, order_id: int = 555) -> None:
        self._order_id = order_id
        self.requests: list[object] = []

    def submit_print_job(self, request: object) -> PrintJob:
        self.requests.append(request)
        return PrintJob(id=self._order_id, status=PrintJobStatus(name="CREATED"))


def _shipping_address() -> ShippingAddress:
    return ShippingAddress(
        name="Alex Morgan",
        street1="1 Main St",
        city="Springfield",
        postcode="00000",
        country_code="US",
        phone_number="+15555555555",
    )


def _printer(client: FakeLuluClient) -> LuluPrinter:
    return LuluPrinter(
        client,  # pyright: ignore[reportArgumentType]
        contact_email="orders@example.com",
        pod_package_id="0850X0850FCPRECW060UW444MXX",
        shipping_address=_shipping_address(),
    )


def test_rejects_local_filesystem_path(session: Session) -> None:
    printer = _printer(FakeLuluClient())
    album = make_album(assembled_pdf_path="/data/albums/honeymoon-2026/book.pdf")
    session.add(album)
    session.commit()

    with pytest.raises(UnreachablePdfError):
        printer.print(album, session)


def test_raises_when_pdf_not_assembled_yet(session: Session) -> None:
    printer = _printer(FakeLuluClient())
    album = make_album()
    session.add(album)
    session.commit()

    with pytest.raises(NotAssembledError, match="assembled_pdf_path"):
        printer.print(album, session)


def test_submits_for_a_reachable_url(session: Session) -> None:
    fake_client = FakeLuluClient(order_id=777)
    printer = _printer(fake_client)
    album = make_album(assembled_pdf_path="https://example.com/albums/honeymoon-2026/book.pdf")
    session.add(album)
    session.commit()

    result = printer.print(album, session)

    assert result.output_ref == "777"
    assert len(fake_client.requests) == 1
