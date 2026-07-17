"""Tests for ``cli.py``'s argument parsing and error handling (build steps 14 +
27, and step 29's captioner selection).

``pipeline()`` and ``ingest_directory()`` are exercised in their own test
modules — this file only checks that ``main()`` wires each subcommand's
arguments through correctly (including the ``--captioner``/``--force`` selection),
and turns expected, user-fixable errors (``FileNotFoundError``, ``ValueError``,
``IngestError``) into a clean message + non-zero exit rather than a raw traceback,
while letting anything else propagate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from album.analysis.captioner import ClaudeCaptioner, TemplateCaptioner
from album.cli import main
from album.ingest import IngestError


@pytest.fixture(autouse=True)
def _default_captioner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``get_settings`` so ``--captioner``-less runs resolve the default without
    needing a real ``.env`` (``DATABASE_URL`` etc.). Individual tests override it."""
    monkeypatch.setattr("album.cli.get_settings", lambda: SimpleNamespace(captioner="template"))


class TestCli:
    """`main`: argument wiring and expected-vs-unexpected error handling."""

    def test_run_calls_pipeline_with_the_given_album_slug(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "album.cli.pipeline",
            lambda **kwargs: calls.append(kwargs["album_slug"]),
        )
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "run", "--album", "honeymoon-2026"]
        )

        main()

        assert calls == ["honeymoon-2026"]

    @pytest.mark.parametrize(
        "error",
        [FileNotFoundError("no album.yml at ..."), ValueError("no album")],
        ids=["file-not-found", "value-error"],
    )
    def test_run_prints_a_clean_message_and_exits_for_expected_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: Exception,
    ) -> None:
        def _raise(**kwargs: object) -> None:
            raise error

        monkeypatch.setattr("album.cli.pipeline", _raise)
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "run", "--album", "honeymoon-2026"]
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert str(error) in capsys.readouterr().err

    def test_run_lets_unexpected_errors_propagate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(**kwargs: object) -> None:
            raise RuntimeError("something genuinely unexpected")

        monkeypatch.setattr("album.cli.pipeline", _raise)
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "run", "--album", "honeymoon-2026"]
        )

        with pytest.raises(RuntimeError, match="something genuinely unexpected"):
            main()

    def test_missing_album_argument_is_rejected_by_argparse(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "run"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2  # argparse's usage-error exit code


class TestCaptionerSelection:
    """Step 29: ``--captioner`` (per-run) overrides the ``CAPTIONER`` env default,
    and both are threaded into ``pipeline`` alongside ``--force`` — the A/B enabler."""

    def _capture_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        captured: dict[str, object] = {}
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: captured.update(kwargs))
        return captured

    @pytest.mark.parametrize("subcommand", ["run", "generate"], ids=["run", "generate"])
    def test_flag_threaded_into_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        subcommand: str,
    ) -> None:
        captured = self._capture_pipeline(monkeypatch)
        monkeypatch.setattr("album.cli.ingest_directory", lambda *a, **k: "slug")
        argv = (
            ["photobook-generator", "run", "--album", "a", "--captioner", "claude", "--force"]
            if subcommand == "run"
            else ["photobook-generator", "generate", ".", "--captioner", "claude", "--force"]
        )
        monkeypatch.setattr(sys, "argv", argv)

        main()

        assert isinstance(captured["captioner"], ClaudeCaptioner)
        assert captured["force"] is True

    def test_flag_overrides_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("album.cli.get_settings", lambda: SimpleNamespace(captioner="template"))
        captured = self._capture_pipeline(monkeypatch)
        monkeypatch.setattr(
            sys,
            "argv",
            ["photobook-generator", "run", "--album", "a", "--captioner", "claude"],
        )

        main()

        assert isinstance(captured["captioner"], ClaudeCaptioner)  # flag wins over env
        assert captured["force"] is False

    def test_default_is_the_env_captioner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("album.cli.get_settings", lambda: SimpleNamespace(captioner="template"))
        captured = self._capture_pipeline(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "run", "--album", "a"])

        main()

        assert isinstance(captured["captioner"], TemplateCaptioner)


class TestGenerate:
    """`photobook-generator generate <dir>`: dir → ingest → pipeline (one command)."""

    def _stub_ingest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        returns: str = "lisbon-trip",
    ) -> list[tuple[tuple[object, ...], dict[str, object]]]:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def _ingest(*args: object, **kwargs: object) -> str:
            calls.append((args, kwargs))
            return returns

        monkeypatch.setattr("album.cli.ingest_directory", _ingest)
        return calls

    def test_ingests_then_runs_pipeline_and_prints_book_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ingest_calls = self._stub_ingest(monkeypatch, returns="lisbon-trip")
        pipeline_calls: list[str] = []
        monkeypatch.setattr(
            "album.cli.pipeline",
            lambda **kwargs: pipeline_calls.append(kwargs["album_slug"]),
        )
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "generate", "./photos"])

        main()

        # The directory was passed as a Path; the returned slug drove the pipeline.
        assert ingest_calls[0][0] == (Path("./photos"),)
        assert pipeline_calls == ["lisbon-trip"]
        # The book path is reported to the user.
        out = capsys.readouterr().out
        assert str(Path("data/albums/lisbon-trip/book/index.html")) in out

    def test_passes_all_options_through_to_ingest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ingest_calls = self._stub_ingest(monkeypatch)
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: None)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "photobook-generator",
                "generate",
                ".",
                "--slug",
                "trip-2026",
                "--title",
                "Our Trip",
                "--timezone",
                "Europe/Warsaw",
                "--source-document",
                "schedule.pdf",
                "--force",
            ],
        )

        main()

        assert ingest_calls[0][1] == {
            "slug": "trip-2026",
            "title": "Our Trip",
            "timezone": "Europe/Warsaw",
            "source_document": Path("schedule.pdf"),
            "force": True,
        }

    @pytest.mark.parametrize(
        "error",
        [
            IngestError("no images found in ..."),
            FileNotFoundError("no album.yml at ..."),
            ValueError("bad timezone"),
        ],
        ids=["ingest-error", "file-not-found", "value-error"],
    )
    def test_prints_a_clean_message_and_exits_for_expected_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: Exception,
    ) -> None:
        def _raise(*args: object, **kwargs: object) -> str:
            raise error

        monkeypatch.setattr("album.cli.ingest_directory", _raise)
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: None)
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "generate", "./photos"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert str(error) in capsys.readouterr().err

    def test_lets_unexpected_errors_propagate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*args: object, **kwargs: object) -> str:
            raise RuntimeError("something genuinely unexpected")

        monkeypatch.setattr("album.cli.ingest_directory", _raise)
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "generate", "./photos"])

        with pytest.raises(RuntimeError, match="something genuinely unexpected"):
            main()

    def test_missing_directory_argument_is_rejected_by_argparse(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "generate"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2  # argparse's usage-error exit code
