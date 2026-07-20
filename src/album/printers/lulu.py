"""``LuluPrinter`` — the opt-in printer that orders a physical book from Lulu.

Renders the assembled book's **master PDF on demand** — from the layout
[`assemble`](../flow/stages/assemble.py) recorded on the ``Photo`` rows (via
[`base.iter_book_pages`](./base.py)) — and submits it to Lulu's Print API,
returning the order id as the :class:`~album.printers.base.PrintResult`. The PDF
is produced **here**, not in ``assemble``: only a real physical order needs it, so
the expensive WeasyPrint render happens on the Lulu path rather than
unconditionally on every pipeline run (the default
:class:`~album.printers.html.HtmlPrinter` renders a webpage and never touches a
PDF).

Beyond rendering, this printer encodes *fulfillment policy* (which product SKU,
which recipient, which shipping level) and delegates the raw HTTP exchange to
:class:`~album.lulu.client.LuluClient`, which owns no policy — mirroring how
``immich/client.py`` never decides what to sync. That's why the policy lives here,
not in ``lulu/client.py``.

Not the default: unlike :class:`~album.printers.html.HtmlPrinter`, ordering a real
book needs live Lulu credentials *and* a PDF hosted at a URL Lulu's servers can
fetch. The rendered PDF is a local file, so a ``publish_pdf`` hook (path → public
URL) must be injected to place it somewhere reachable; without one, submission is
refused with :class:`UnreachablePdfError` — see its docstring for the known gap.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session
from weasyprint import HTML

from album.config import get_settings
from album.db.models import Album
from album.lulu.client import LuluClient, PrintJobRequest, ShippingAddress
from album.printers.base import BookPage, PrintResult, iter_book_pages

# --- Lulu print spec (open question 4, resolved step 13) -------------------
# Square 8.5x8.5in trim is a standard Lulu photo-book size. Bleed and the page
# box are from Lulu's Print API docs (Book Creation Guide / PDF Creation
# Settings). The rendered page = trim + 2*bleed on every edge; the further
# safety-margin inset keeps photos/captions off the trim edge.
TRIM_WIDTH_IN = 8.5
TRIM_HEIGHT_IN = 8.5
BLEED_IN = 0.125
SAFETY_MARGIN_IN = 0.5

PAGE_WIDTH_IN = TRIM_WIDTH_IN + 2 * BLEED_IN
PAGE_HEIGHT_IN = TRIM_HEIGHT_IN + 2 * BLEED_IN

# One grid template per per-page photo count `assemble` ever produces (its
# _LAYOUT_CAPACITIES). A page with an unexpected count means the chunker's
# invariant broke — fail loudly rather than guess a layout.
_LAYOUT_TEMPLATES: dict[int, str] = {
    1: "layout_1up.html",
    2: "layout_2up.html",
    3: "layout_3up.html",
}

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"

# A hook that puts the rendered local PDF somewhere Lulu's servers can fetch and
# returns that public URL (serve `data/albums/` statically, push to S3, …).
PdfPublisher = Callable[[Path], str]


class UnreachablePdfError(ValueError):
    """The rendered master PDF isn't hosted at a URL Lulu can fetch.

    Lulu's ``source_url`` fields must be an HTTP(S) URL its servers can reach;
    :class:`LuluPrinter` renders the PDF to a local file. Nothing in this pipeline
    yet stands up a place to host it, so with no ``publish_pdf`` hook (or one that
    returns a non-HTTP path) submission is refused loudly rather than sending Lulu
    a ``source_url`` it can't resolve. Wiring a real publisher (serve
    ``data/albums/`` statically, push to S3, …) is the follow-up that makes a real
    Lulu order possible.
    """


class LuluPrinter:
    """Renders an album's master PDF on demand and submits it to Lulu. Implements
    :class:`~album.printers.base.Printer`.

    Policy (recipient + product SKU) is passed in explicitly rather than read
    from :func:`~album.config.get_settings` inside :meth:`print`, so behavior
    depends on constructor arguments, not ambient environment state (same
    reasoning as ``LuluClient`` taking explicit credentials). Use
    :meth:`from_settings` to build the real one; today that means one recipient
    and one SKU for the whole deployment — a real limitation if ``fulfill`` ever
    serves more than one recipient, acceptable for a single-book run. The SKU
    also needs confirming against Lulu's real trim size before a real order.

    ``output_dir`` mirrors ``HtmlPrinter``'s: the PDF is written to
    ``<output_dir>/<slug>/assembled.pdf`` and its path recorded on
    ``Album.assembled_pdf_path``. ``publish_pdf`` (path → public URL) is the seam
    that makes a real order possible — ``None`` is the known-gap default that
    raises :class:`UnreachablePdfError` after rendering.
    """

    def __init__(
        self,
        client: LuluClient,
        *,
        contact_email: str,
        pod_package_id: str,
        shipping_address: ShippingAddress,
        output_dir: str | Path = "data/albums",
        publish_pdf: PdfPublisher | None = None,
        templates_dir: Path = _TEMPLATES_DIR,
    ) -> None:
        self._client = client
        self._contact_email = contact_email
        self._pod_package_id = pod_package_id
        self._shipping_address = shipping_address
        self._output_dir = Path(output_dir)
        self._publish_pdf = publish_pdf
        self._env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(["html"]),
        )

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
        # iter_book_pages raises NotAssembledError if `assemble` hasn't run.
        pages = iter_book_pages(album, session)

        pdf_path = self._output_dir / album.slug / "assembled.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        self._render_pdf(album.title, pages, pdf_path)
        album.assembled_pdf_path = str(pdf_path)

        source_url = self._reachable_url(pdf_path)
        request = PrintJobRequest(
            contact_email=self._contact_email,
            shipping_level="MAIL",
            line_items=[
                {
                    "quantity": 1,
                    "pod_package_id": self._pod_package_id,
                    "interior": {"source_url": source_url},
                    "cover": {"source_url": source_url},
                },
            ],
            shipping_address=self._shipping_address,
        )
        print_job = self._client.submit_print_job(request)
        return PrintResult(output_ref=str(print_job.id))

    def _reachable_url(self, pdf_path: Path) -> str:
        """Publish the rendered PDF and return a URL Lulu can fetch, or raise the
        known-gap :class:`UnreachablePdfError` when no publisher is wired."""
        if self._publish_pdf is None:
            raise UnreachablePdfError(
                f"{pdf_path} is a local file, not a URL Lulu can fetch — inject a "
                "publish_pdf hook to host it; see UnreachablePdfError for the known gap",
            )
        url = self._publish_pdf(pdf_path)
        if not url.startswith(("http://", "https://")):
            raise UnreachablePdfError(
                f"publish_pdf returned {url!r}, not an http(s) URL Lulu can fetch",
            )
        return url

    def _render_pdf(self, album_title: str, pages: list[BookPage], output_path: Path) -> None:
        """Render the book's pages to a single print-ready PDF via WeasyPrint.

        Rebuilds ``assemble``'s page structure from the reader: a dedicated
        section-header page precedes each segment's first page (``starts_segment``),
        then each page's photos are laid into the grid template for their count.
        """
        base_ctx = {
            "album_title": album_title,
            "page_width_in": PAGE_WIDTH_IN,
            "page_height_in": PAGE_HEIGHT_IN,
            "bleed_in": BLEED_IN,
            "safety_margin_in": SAFETY_MARGIN_IN,
        }
        html_pages = [html for page in pages for html in self._page_html(base_ctx, page)]
        _write_pdf(html_pages, output_path)

    def _page_html(self, base_ctx: dict[str, object], page: BookPage) -> list[str]:
        """The HTML for one book page: an optional section header (on a segment's
        first page) followed by the grid layout for its photos."""
        html_pages: list[str] = []
        if page.starts_segment:
            html_pages.append(
                self._env.get_template("section.html").render(
                    **base_ctx,
                    label=page.segment_label,
                    place=page.segment_place,
                ),
            )
        html_pages.append(
            self._env.get_template(self._layout_template(len(page.photos))).render(
                **base_ctx,
                photos=page.photos,
            ),
        )
        return html_pages

    @staticmethod
    def _layout_template(photo_count: int) -> str:
        template = _LAYOUT_TEMPLATES.get(photo_count)
        if template is None:
            raise ValueError(f"no layout template for a page of {photo_count} photo(s)")
        return template


def _write_pdf(html_pages: list[str], output_path: Path) -> None:
    """Combine per-page HTML into one PDF via WeasyPrint.

    ``base_url="."`` resolves each page's ``<img src>`` (``Photo.rendered_path``)
    against the process's working directory, matching how that path is stored —
    not the templates dir, which holds no photos.
    """
    documents = [HTML(string=html, base_url=".").render() for html in html_pages]
    all_pages = [page for doc in documents for page in doc.pages]
    # weasyprint's inferred stub types `pages` as `str` (from its `='all'` default)
    # even though `Document.copy` actually accepts any iterable of `Page` objects
    # (see its docstring) — a third-party stub gap, not a real type error.
    documents[0].copy(all_pages).write_pdf(str(output_path))  # pyright: ignore[reportArgumentType]
