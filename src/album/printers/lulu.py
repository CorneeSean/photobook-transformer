"""``LuluPrinter`` — the opt-in printer that orders a physical book from Lulu.

Submits the assembled master PDF ([`assemble`](../stages/assemble.py) writes
``Album.assembled_pdf_path``) to Lulu's Print API and returns the order id as the
:class:`~album.printers.base.PrintResult`. It encodes *fulfillment policy* (which
product SKU, which recipient, which shipping level) and delegates the raw HTTP
exchange to :class:`~album.lulu.client.LuluClient`, which owns no policy — mirroring
how ``immich/client.py`` never decides what to sync. That's why the policy lives
here, not in ``lulu/client.py``.

Not the default: unlike :class:`~album.printers.html.HtmlPrinter`, ordering a real
book needs live Lulu credentials *and* a PDF hosted at a URL Lulu's servers can
fetch — see :class:`UnreachablePdfError`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from album.config import get_settings
from album.db.models import Album
from album.lulu.client import LuluClient, PrintJobRequest, ShippingAddress
from album.printers.base import NotAssembledError, PrintResult


class UnreachablePdfError(ValueError):
    """``assembled_pdf_path`` is a local filesystem path, not a URL Lulu can fetch.

    Lulu's ``source_url`` fields must be an HTTP(S) URL its servers can reach;
    ``assemble`` writes a local path. Nothing in this pipeline yet stands up a
    place to host that file, so submission is refused loudly rather than sending
    Lulu a ``source_url`` it can't resolve. Wiring an upload/hosting step (serve
    ``data/albums/`` statically, push to S3, …) is the follow-up that makes a
    real Lulu order possible.
    """


class LuluPrinter:
    """Submits an album's assembled PDF to Lulu. Implements
    :class:`~album.printers.base.Printer`.

    Policy (recipient + product SKU) is passed in explicitly rather than read
    from :func:`~album.config.get_settings` inside :meth:`print`, so behavior
    depends on constructor arguments, not ambient environment state (same
    reasoning as ``LuluClient`` taking explicit credentials). Use
    :meth:`from_settings` to build the real one; today that means one recipient
    and one SKU for the whole deployment — a real limitation if ``fulfill`` ever
    serves more than one recipient, acceptable for a single-book run. The SKU
    also needs confirming against Lulu's real trim size before a real order.
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
    def from_settings(cls, client: LuluClient) -> LuluPrinter:
        settings = get_settings()
        return cls(
            client,
            contact_email=settings.lulu.contact_email,
            pod_package_id=settings.lulu.pod_package_id,
            shipping_address=ShippingAddress(
                name=settings.lulu.shipping_name,
                street1=settings.lulu.shipping_street1,
                city=settings.lulu.shipping_city,
                postcode=settings.lulu.shipping_postcode,
                country_code=settings.lulu.shipping_country_code,
                phone_number=settings.lulu.shipping_phone_number,
            ),
        )

    def print(self, album: Album, session: Session) -> PrintResult:
        pdf_path = album.assembled_pdf_path
        if pdf_path is None:
            raise NotAssembledError(
                f"album {album.slug!r} has no assembled_pdf_path — run `assemble` before printing",
            )
        if not pdf_path.startswith(("http://", "https://")):
            raise UnreachablePdfError(
                f"{pdf_path!r} is not an http(s) URL Lulu can fetch — "
                "see UnreachablePdfError for the known gap",
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
                },
            ],
            shipping_address=self._shipping_address,
        )
        print_job = self._client.submit_print_job(request)
        return PrintResult(output_ref=str(print_job.id))
