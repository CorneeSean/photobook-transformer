"""Tests for :class:`~album.flow.stages._progress.ProgressLogger`.

The logger is a time-throttled heartbeat over a long per-photo loop: one INFO line
at most per ``interval_s``, regardless of how many items are ticked between. The
clock is injected so the throttle is deterministic without ``sleep``.
"""

from __future__ import annotations

import logging

import pytest

from album.flow.stages._progress import ProgressLogger

_log = logging.getLogger("test.progress")


class _FakeClock:
    """A monotonic clock whose value is advanced explicitly by the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestProgressLogger:
    def test_throttles_within_the_interval(self, caplog: pytest.LogCaptureFixture) -> None:
        clock = _FakeClock()
        progress = ProgressLogger(_log, "quality album=demo", interval_s=30.0, clock=clock)

        with caplog.at_level(logging.INFO, logger="test.progress"):
            progress.tick()  # t=0: within interval of construction -> no emit
            clock.now = 29.0
            progress.tick()  # still within 30s -> no emit
            assert caplog.records == []

            clock.now = 30.0
            progress.tick()  # interval elapsed -> one emit

        assert len(caplog.records) == 1
        assert "quality album=demo" in caplog.records[0].message
        assert "3 processed" in caplog.records[0].message  # running count, no total

    def test_reports_total_when_given(self, caplog: pytest.LogCaptureFixture) -> None:
        clock = _FakeClock()
        progress = ProgressLogger(
            _log, "render album=demo", total=100, interval_s=10.0, clock=clock
        )

        with caplog.at_level(logging.INFO, logger="test.progress"):
            for _ in range(5):
                progress.tick()
            clock.now = 10.0
            progress.tick()

        assert len(caplog.records) == 1
        assert "6/100 processed" in caplog.records[0].message

    def test_re_throttles_after_each_emit(self, caplog: pytest.LogCaptureFixture) -> None:
        clock = _FakeClock()
        progress = ProgressLogger(_log, "caption", interval_s=10.0, clock=clock)

        with caplog.at_level(logging.INFO, logger="test.progress"):
            clock.now = 10.0
            progress.tick()  # emit #1
            clock.now = 15.0
            progress.tick()  # only 5s since last emit -> no emit
            clock.now = 20.0
            progress.tick()  # 10s since last emit -> emit #2

        assert len(caplog.records) == 2
