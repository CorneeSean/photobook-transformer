"""``fulfill`` — submit the assembled PDF to Lulu's Print API, scoped by ``album_id``.

Reads [`Album.assembled_pdf_path`](../db/models.py) (populated by
[`assemble`](./assemble.py)) and submits it to Lulu, storing the returned order id
on ``Album.lulu_order_id``. That column's presence is what makes this idempotent:
a second run is a no-op unless ``force=True``.

No Prefect imports live here — this is a plain function so it stays unit-testable
without an orchestrator (folder-structure.md rule).

**Known gap, flagged plainly rather than papered over:** Lulu's ``source_url``
fields must be an HTTP(S) URL its servers can fetch; ``Album.assembled_pdf_path``
is a local filesystem path (WeasyPrint/Playwright's render target — see
assemble.md). Nothing in this build step stands up a place to host that file, so
:class:`LuluPrintJobAdapter` raises :class:`UnreachablePdfError` for a path that
isn't already an ``http(s)://`` URL rather than silently submitting a broken
request. Wiring an upload/hosting step (e.g. serving ``data/albums/`` statically,
or pushing to S3) is follow-up work for whoever wires the real flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from album.config import get_settings
from album.db.models import Album
from album.lulu.client import LuluClient, PrintJobRequest, ShippingAddress


class PrintJobSubmitter(Protocol):
    """The slice of Lulu integration that ``fulfill`` needs to submit a job.

    Declared as a Protocol so tests can pass a fake returning a canned order id
    without an HTTP layer; :class:`LuluPrintJobAdapter` (below) is the real
    implementation, wrapping :class:`~album.lulu.client.LuluClient`.
    """

    def submit(self, pdf_path: str) -> str: ...


class UnreachablePdfError(ValueError):
    """``pdf_path`` is a local filesystem path, not a URL Lulu's servers can fetch.

    See the module docstring's "known gap" note: this step doesn't wire up
    hosting/uploading the assembled PDF anywhere reachable, so submission is
    refused loudly rather than sending Lulu a ``source_url`` it can't resolve.
    """


class LuluPrintJobAdapter:
    """Adapts :class:`~album.lulu.client.LuluClient` to :class:`PrintJobSubmitter`.

    Lives here, not in ``album.lulu.client``, because it encodes *fulfillment
    policy* (what product SKU, what recipient, what shipping level) rather than
    HTTP transport — ``lulu/client.py`` stays a thin, policy-free wrapper, mirroring
    how ``immich/client.py`` never decides what to sync.

    Policy (recipient + product SKU) is passed in explicitly, not read from
    :func:`~album.config.get_settings` inside :meth:`submit` — that would make
    this class's behavior depend on ambient environment state instead of its
    constructor arguments, same reasoning as ``LuluClient`` taking an explicit
    ``base_url``/credentials rather than calling ``get_settings()`` itself. Use
    :meth:`from_settings` to build the real one from :class:`~album.config.Settings`
    placeholders (``lulu_contact_email``, ``lulu_pod_package_id``,
    ``lulu_shipping_*``); today that means one recipient and one product SKU for
    the whole deployment. That's a real limitation — a second concurrent order to
    a different address needs a real per-order parameter, not a global setting —
    acceptable for a single-book, single-run pipeline today but worth revisiting if
    `fulfill` ever needs to serve more than one recipient. The SKU also needs
    confirming against Lulu's real trim size (open question #4) before a real
    order.
    """

    def __init__(
        self,
        client: LuluClient,
        *,
        contact_email: str,
        pod_package_id: str,
        shipping_address: ShippingAddress,
    ) -> None:
        self._client = client
        self._contact_email = contact_email
        self._pod_package_id = pod_package_id
        self._shipping_address = shipping_address

    @classmethod
    def from_settings(cls, client: LuluClient) -> LuluPrintJobAdapter:
        settings = get_settings()
        return cls(
            client,
            contact_email=settings.lulu_contact_email,
            pod_package_id=settings.lulu_pod_package_id,
            shipping_address=ShippingAddress(
                name=settings.lulu_shipping_name,
                street1=settings.lulu_shipping_street1,
                city=settings.lulu_shipping_city,
                postcode=settings.lulu_shipping_postcode,
                country_code=settings.lulu_shipping_country_code,
                phone_number=settings.lulu_shipping_phone_number,
            ),
        )

    def submit(self, pdf_path: str) -> str:
        if not pdf_path.startswith(("http://", "https://")):
            raise UnreachablePdfError(
                f"{pdf_path!r} is not an http(s) URL Lulu can fetch — "
                "see fulfill.py's module docstring for the known gap"
            )

        request = PrintJobRequest(
            contact_email=self._contact_email,
            shipping_level="MAIL",
            line_items=[
                {
                    "quantity": 1,
                    "pod_package_id": self._pod_package_id,
                    "interior": {"source_url": pdf_path},
                    "cover": {"source_url": pdf_path},
                }
            ],
            shipping_address=self._shipping_address,
        )
        print_job = self._client.submit_print_job(request)
        return str(print_job.id)


@dataclass
class FulfillResult:
    submitted: bool
    lulu_order_id: str | None = None


def fulfill(
    album_id: int,
    session: Session,
    client: PrintJobSubmitter,
    *,
    force: bool = False,
) -> FulfillResult:
    """Submit ``album_id``'s assembled PDF to Lulu. Idempotent unless ``force=True``."""
    album = session.get(Album, album_id)
    if album is None:
        raise ValueError(f"no album with id {album_id}")

    if album.lulu_order_id is not None and not force:
        return FulfillResult(submitted=False, lulu_order_id=album.lulu_order_id)

    if album.assembled_pdf_path is None:
        raise ValueError(
            f"album {album.slug!r} has no assembled_pdf_path set — "
            "run the `assemble` stage before `fulfill`"
        )

    order_id = client.submit(album.assembled_pdf_path)
    album.lulu_order_id = order_id
    session.flush()
    return FulfillResult(submitted=True, lulu_order_id=order_id)
