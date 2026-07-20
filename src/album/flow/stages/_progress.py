"""``ProgressLogger`` — a time-throttled heartbeat for a long per-photo stage loop.

The four per-photo stages (``quality``/``enhance``/``render``/``caption``) iterate
thousands of photos. Logging one line per photo would be noise; logging nothing
leaves a multi-minute stage looking hung. :class:`ProgressLogger` splits the
difference: call :meth:`tick` once per processed item and it emits an INFO line at
most once per ``interval_s`` (default 30 s).

Deliberately stdlib :mod:`logging`, **not** Prefect's ``get_run_logger()`` — these
stages are Prefect-free logic (folder-structure.md) and must stay runnable via
``stage.run()`` outside a Prefect context. The clock is injected so the throttle is
unit-testable without ``sleep``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from logging import Logger


class ProgressLogger:
    """Throttled progress heartbeat over a per-photo loop (INFO, ``interval_s`` cap).

    Construct with the target logger, a human ``label``, and optionally the ``total``
    item count; call :meth:`tick` once per processed item.
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
