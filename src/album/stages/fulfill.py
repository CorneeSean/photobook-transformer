"""``fulfill`` — deliver the assembled book via a pluggable ``Printer``, scoped by ``album_id``.

The final stage: it turns the laid-out book into a real deliverable. It owns no
delivery policy of its own — it delegates to an injected
[`Printer`](../printers/base.py) (an :class:`~album.printers.html.HtmlPrinter`
browsable webpage by default, an :class:`~album.printers.lulu.LuluPrinter`
physical Lulu order opt-in) and records the printer-agnostic reference the printer
returns on ``Album.output_ref``. That column's presence is what makes this
idempotent: a second run is a no-op unless ``force=True``.

Each printer validates its own precondition (a webpage needs the placed photos; a
Lulu order needs the master PDF), so this stage stays medium-agnostic. No Prefect
imports live here — this is a plain function so it stays unit-testable without an
orchestrator (folder-structure.md rule).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from album.db.models import Album
from album.printers.base import Printer


@dataclass
class FulfillResult:
    printed: bool
    output_ref: str | None = None


def fulfill(
    album_id: int,
    session: Session,
    printer: Printer,
    *,
    force: bool = False,
) -> FulfillResult:
    """Deliver ``album_id``'s assembled book via ``printer``. Idempotent unless ``force=True``."""
    album = session.get(Album, album_id)
    if album is None:
        raise ValueError(f"no album with id {album_id}")

    if album.output_ref is not None and not force:
        return FulfillResult(printed=False, output_ref=album.output_ref)

    result = printer.print(album, session)
    album.output_ref = result.output_ref
    session.flush()
    return FulfillResult(printed=True, output_ref=result.output_ref)
