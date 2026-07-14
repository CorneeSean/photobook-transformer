"""Tests for ``cli.py``'s argument parsing and error handling (build step 14).

``pipeline()`` itself is exercised in ``tests/test_flow.py`` — this file only
checks that ``main()`` wires ``--album`` through correctly and turns expected,
user-fixable errors (``FileNotFoundError``, ``ValueError``) into a clean
message + non-zero exit rather than a raw traceback, while letting anything
else propagate.
"""

from __future__ import annotations

import sys

import pytest

from album.cli import main


def test_run_calls_pipeline_with_the_given_album_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("album.cli.pipeline", lambda album_slug: calls.append(album_slug))
    monkeypatch.setattr(sys, "argv", ["album", "run", "--album", "honeymoon-2026"])

    main()

    assert calls == ["honeymoon-2026"]


@pytest.mark.parametrize(
    "error", [FileNotFoundError("no album.yml at ..."), ValueError("no album")]
)
def test_run_prints_a_clean_message_and_exits_for_expected_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    def _raise(album_slug: str) -> None:
        raise error

    monkeypatch.setattr("album.cli.pipeline", _raise)
    monkeypatch.setattr(sys, "argv", ["album", "run", "--album", "honeymoon-2026"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert str(error) in capsys.readouterr().err


def test_run_lets_unexpected_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(album_slug: str) -> None:
        raise RuntimeError("something genuinely unexpected")

    monkeypatch.setattr("album.cli.pipeline", _raise)
    monkeypatch.setattr(sys, "argv", ["album", "run", "--album", "honeymoon-2026"])

    with pytest.raises(RuntimeError, match="something genuinely unexpected"):
        main()


def test_missing_album_argument_is_rejected_by_argparse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["album", "run"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2  # argparse's usage-error exit code
