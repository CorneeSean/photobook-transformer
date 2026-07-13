"""Tests for the ``fulfill`` stage over in-memory SQLite.

The Lulu HTTP layer is stubbed by a fake ``PrintJobSubmitter`` returning a canned
order id, so the ``fulfill()`` logic tests stay pure-logic and fast
(working-agreement.md); the real ``LuluClient`` HTTP exchange is covered
separately against recorded fixtures in test_lulu_client.py.
``LuluPrintJobAdapter`` (the glue between the two) gets its own focused tests
below, against a fake ``LuluClient``-shaped object rather than real HTTP.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from album.db.models import Album
from album.lulu.client import PrintJob, PrintJobStatus, ShippingAddress
from album.stages.fulfill import LuluPrintJobAdapter, UnreachablePdfError, fulfill

from .conftest import make_album


class FakeLulu:
    """An in-memory :class:`~album.stages.fulfill.PrintJobSubmitter` for tests."""

    def __init__(self, order_id: str = "lulu-order-1") -> None:
        self._order_id = order_id
        self.calls: list[str] = []

    def submit(self, pdf_path: str) -> str:
        self.calls.append(pdf_path)
        return self._order_id


def _persist(session: Session, album: Album) -> int:
    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


def test_submits_assembled_pdf_and_stores_order_id(session: Session) -> None:
    album_id = _persist(
        session, make_album(assembled_pdf_path="/data/albums/honeymoon-2026/book.pdf")
    )
    client = FakeLulu(order_id="lulu-order-42")

    result = fulfill(album_id, session, client)

    assert result.submitted is True
    assert result.lulu_order_id == "lulu-order-42"
    assert client.calls == ["/data/albums/honeymoon-2026/book.pdf"]
    album = session.get(Album, album_id)
    assert album is not None
    assert album.lulu_order_id == "lulu-order-42"


def test_idempotent_skips_resubmission(session: Session) -> None:
    album_id = _persist(
        session,
        make_album(
            assembled_pdf_path="/data/albums/honeymoon-2026/book.pdf",
            lulu_order_id="already-submitted",
        ),
    )
    client = FakeLulu(order_id="should-not-be-used")

    result = fulfill(album_id, session, client)

    assert result.submitted is False
    assert result.lulu_order_id == "already-submitted"
    assert client.calls == []
    album = session.get(Album, album_id)
    assert album is not None
    assert album.lulu_order_id == "already-submitted"


def test_force_resubmits_even_if_already_submitted(session: Session) -> None:
    album_id = _persist(
        session,
        make_album(
            assembled_pdf_path="/data/albums/honeymoon-2026/book.pdf",
            lulu_order_id="old-order",
        ),
    )
    client = FakeLulu(order_id="new-order")

    result = fulfill(album_id, session, client, force=True)

    assert result.submitted is True
    assert result.lulu_order_id == "new-order"
    assert client.calls == ["/data/albums/honeymoon-2026/book.pdf"]
    album = session.get(Album, album_id)
    assert album is not None
    assert album.lulu_order_id == "new-order"


def test_raises_when_pdf_not_assembled_yet(session: Session) -> None:
    album_id = _persist(session, make_album())
    client = FakeLulu()

    with pytest.raises(ValueError, match="assembled_pdf_path"):
        fulfill(album_id, session, client)

    assert client.calls == []


def test_raises_on_unknown_album(session: Session) -> None:
    client = FakeLulu()

    with pytest.raises(ValueError, match="no album"):
        fulfill(999, session, client)


class FakeLuluClient:
    """A fake shaped like :class:`~album.lulu.client.LuluClient` for adapter tests."""

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


def _adapter(client: FakeLuluClient) -> LuluPrintJobAdapter:
    return LuluPrintJobAdapter(
        client,  # pyright: ignore[reportArgumentType]
        contact_email="orders@example.com",
        pod_package_id="0850X0850FCPRECW060UW444MXX",
        shipping_address=_shipping_address(),
    )


def test_adapter_rejects_local_filesystem_path() -> None:
    adapter = _adapter(FakeLuluClient())

    with pytest.raises(UnreachablePdfError):
        adapter.submit("/data/albums/honeymoon-2026/book.pdf")


def test_adapter_submits_for_a_reachable_url() -> None:
    fake_client = FakeLuluClient(order_id=777)
    adapter = _adapter(fake_client)

    order_id = adapter.submit("https://example.com/albums/honeymoon-2026/book.pdf")

    assert order_id == "777"
    assert len(fake_client.requests) == 1
