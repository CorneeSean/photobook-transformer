"""``fulfill`` — deliver the assembled book via a pluggable ``Printer``, scoped by ``album_id``.

The final stage: it turns the laid-out book into a real deliverable. It owns no
delivery policy of its own — it delegates to an injected
[`Printer`](../printers/base.py) (an :class:`~album.printers.html.HtmlPrinter`
browsable webpage by default, an :class:`~album.printers.lulu.LuluPrinter`
physical Lulu order opt-in) and records the printer-agnostic reference the printer
returns on ``Album.output_ref``. That column's presence is what makes this
idempotent: a second run is a no-op unless ``force=True``.

Each printer renders its own medium from the placed photos on demand (a webpage,
or the master PDF a Lulu order submits), so this stage stays medium-agnostic.
:class:`FulfillStage` stays Prefect-free logic — so it's unit-testable without an
orchestrator via ``run`` — and runs as a Prefect task through the ``run_as_task`` /
``to_task`` glue it inherits from :class:`~album.flow.stages.base.TaskUnit`
(folder-structure.md rule; quality-practices.md §1.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from album.db.session import SessionFactory
from album.flow.stages.base import Stage
from album.printers.base import Printer


@dataclass
class FulfillResult:
    printed: bool
    output_ref: str | None = None


class FulfillStage(Stage[FulfillResult]):
    """`fulfill` — deliver the assembled book via a pluggable ``Printer``. Idempotent.

    Owns no delivery policy of its own: it delegates to the injected
    :class:`~album.printers.base.Printer` and records the printer-agnostic
    reference it returns on ``Album.output_ref``. The ``album_id`` + request-scoped
    ``Session`` are constructor arguments (base
    :class:`~album.flow.stages.base.Stage`); a second run is a no-op unless
    ``force=True``.
    """

    task_name: ClassVar[str] = "fulfill"

    def __init__(
        self,
        printer: Printer,
        album_id: int,
        session_factory: SessionFactory,
        *,
        force: bool = False,
    ) -> None:
        super().__init__(album_id, session_factory, force=force)
        self._printer = printer

    def _process(self) -> FulfillResult:
        """Deliver ``self._album``'s assembled book. Idempotent unless ``force=True``."""
        album, session = self._album, self._session
        if album.output_ref is not None and not self._force:
            return FulfillResult(printed=False, output_ref=album.output_ref)

        result = self._printer.print(album, session)
        album.output_ref = result.output_ref
        return FulfillResult(printed=True, output_ref=result.output_ref)
