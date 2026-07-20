"""``ProgressLogger`` — a time-throttled heartbeat for a long per-photo stage loop.

The four per-photo stages (``quality``/``enhance``/``render``/``caption``) iterate
thousands of photos. Logging one line per photo would be noise; logging nothing
leaves a multi-minute stage looking hung. :class:`ProgressLogger` splits the
difference: call :meth:`tick` once per processed item and it emits an INFO line at
most once per ``interval_s`` (default 30 s).

Used as a context manager it also brackets the loop with two fixed markers —
``--- LOG STARTED ---`` / ``--- LOG FINISHED ---`` — and two guaranteed touchpoints:
the ``0/total`` line on entry and the final ``total/total`` line on exit, so even a
loop that finishes inside one throttle window still shows its start and end::

    with ProgressLogger(_log, f"render album={slug}", total=len(photos)) as progress:
        for photo in photos:
            ...
            progress.tick()

Deliberately stdlib :mod:`logging`, **not** Prefect's ``get_run_logger()`` — these
stages are Prefect-free logic (folder-structure.md) and must stay runnable via
``stage.run()`` outside a Prefect context. The clock is injected so the throttle is
unit-testable without ``sleep``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from logging import Logger
from types import TracebackType


class ProgressLogger:
    """Throttled progress heartbeat over a per-photo loop (INFO, ``interval_s`` cap).

    Construct with the target logger, a human ``label``, and optionally the ``total``
    item count; call :meth:`tick` once per processed item. Enter it as a context
    manager (``with ProgressLogger(...) as progress:``) so the loop is bracketed by
    the ``--- LOG STARTED ---`` / ``--- LOG FINISHED ---`` markers and the guaranteed
    ``0/total`` and ``total/total`` touchpoints.
    """

    def __init__(
        self,
        logger: Logger,
        label: str,
        *,
        total: int | None = None,
        interval_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._logger = logger
        self._label = label
        self._total = total
        self._interval = interval_s
        self._clock = clock
        self._last = clock()
        self._count = 0

    def __enter__(self) -> ProgressLogger:
        """Emit the ``--- LOG STARTED ---`` marker and the ``0/total`` touchpoint."""
        self._logger.info("%s: --- LOG STARTED ---", self._label)
        self._logger.info("%s", self._render())
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Emit the final ``total/total`` touchpoint and the ``--- LOG FINISHED ---``
        marker — always, even if the loop exits via an exception (which still
        propagates, since this returns ``None``)."""
        self._logger.info("%s", self._render())
        self._logger.info("%s: --- LOG FINISHED ---", self._label)

    def tick(self) -> None:
        """Record one processed item, emitting a throttled progress line if due."""
        self._count += 1
        now = self._clock()
        if now - self._last >= self._interval:
            self._last = now
            self._logger.info("%s", self._render())

    def _render(self) -> str:
        if self._total is not None:
            return f"{self._label}: {self._count}/{self._total} processed"
        return f"{self._label}: {self._count} processed"


__all__ = ["ProgressLogger"]
