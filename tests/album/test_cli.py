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
from album.ingest import AlbumOutcome, IngestError
from album.manifest import AlbumSpec, Manifest


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

    def test_run_lead_face_patches_album_yml_before_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        order: list[str] = []
        patch_calls: list[tuple[Path, list[Path]]] = []

        def _patch(album_yml: Path, images: list[Path]) -> None:
            patch_calls.append((album_yml, images))
            order.append("patch")

        monkeypatch.setattr("album.cli.patch_lead_faces", _patch)
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: order.append("pipeline"))
        monkeypatch.setattr(
            sys,
            "argv",
            ["photobook-generator", "run", "--album", "trip", "--lead-face", "alex.jpg"],
        )

        main()

        # Leads are patched onto the existing album.yml *before* the pipeline runs.
        assert order == ["patch", "pipeline"]
        assert patch_calls == [(Path("data/albums/trip/album.yml"), [Path("alex.jpg")])]

    def test_run_without_lead_face_does_not_patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patched = []
        monkeypatch.setattr("album.cli.patch_lead_faces", lambda *a: patched.append(a))
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: None)
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "run", "--album", "trip"])

        main()

        assert patched == []

    def test_run_lead_face_patch_error_is_clean_and_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _raise(album_yml: Path, images: list[Path]) -> None:
            raise IngestError("no album.yml at data/albums/trip/album.yml — run `generate` first")

        monkeypatch.setattr("album.cli.patch_lead_faces", _raise)
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: None)
        monkeypatch.setattr(
            sys,
            "argv",
            ["photobook-generator", "run", "--album", "trip", "--lead-face", "alex.jpg"],
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "no album.yml" in capsys.readouterr().err


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
            "lead_face_images": None,
        }

    def test_lead_face_flag_is_repeatable_and_threaded_into_ingest(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ingest_calls = self._stub_ingest(monkeypatch)
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: None)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "photobook-generator",
                "generate",
                "./photos",
                "--lead-face",
                "alex.jpg",
                "--lead-face",
                "sam.jpg",
            ],
        )

        main()

        # Repeated --lead-face accumulates into a list of Paths.
        assert ingest_calls[0][1]["lead_face_images"] == [Path("alex.jpg"), Path("sam.jpg")]

    def test_no_lead_face_flag_passes_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ingest_calls = self._stub_ingest(monkeypatch)
        monkeypatch.setattr("album.cli.pipeline", lambda **kwargs: None)
        monkeypatch.setattr(sys, "argv", ["photobook-generator", "generate", "./photos"])

        main()

        assert ingest_calls[0][1]["lead_face_images"] is None

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


class TestGenerateManifest:
    """`generate <root> --manifest`: fan a tree into N albums → N books, with
    per-album-flag rejection, path resolution, and per-album isolation."""

    def _outcome(self, path: str, slug: str | None, error: str | None = None) -> AlbumOutcome:
        return AlbumOutcome(AlbumSpec(path=path), slug, error)

    def _patch_manifest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        outcomes: list[AlbumOutcome],
    ) -> None:
        """Patch load_manifest + ingest_manifest so no disk/Immich is touched; the
        fan-out returns the given outcomes."""
        monkeypatch.setattr(
            "album.cli.load_manifest",
            lambda _path: Manifest(albums=[AlbumSpec(path="a")]),
        )
        monkeypatch.setattr("album.cli.ingest_manifest", lambda *a, **k: outcomes)

    @pytest.mark.parametrize(
        "flag",
        [
            ["--slug", "x"],
            ["--title", "X"],
            ["--source-document", "d.pdf"],
            ["--lead-face", "r.jpg"],
        ],
        ids=["slug", "title", "source-document", "lead-face"],
    )
    def test_per_album_flags_are_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        flag: list[str],
    ) -> None:
        # A per-album flag in manifest mode is a clear error before any work.
        called = []
        monkeypatch.setattr("album.cli.ingest_manifest", lambda *a, **k: called.append(1) or [])
        monkeypatch.setattr(
            sys,
            "argv",
            ["photobook-generator", "generate", "./photos", "--manifest", *flag],
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "per-album" in capsys.readouterr().err
        assert called == []  # rejected before the fan-out ran

    @pytest.mark.parametrize(
        ("manifest_args", "expected"),
        [
            ([], Path("photos/ingest.yml")),
            (["sub/other.yml"], Path("photos/sub/other.yml")),
            (["/abs/x.yml"], Path("/abs/x.yml")),
        ],
        ids=["default", "relative", "absolute"],
    )
    def test_manifest_path_resolution(
        self,
        monkeypatch: pytest.MonkeyPatch,
        manifest_args: list[str],
        expected: Path,
    ) -> None:
        captured: dict[str, Path] = {}

        def _load(path: Path) -> Manifest:
            captured["path"] = path
            return Manifest(albums=[AlbumSpec(path="a")])

        monkeypatch.setattr("album.cli.load_manifest", _load)
        monkeypatch.setattr(
            "album.cli.ingest_manifest",
            lambda *a, **k: [self._outcome("a", "a")],
        )
        monkeypatch.setattr("album.cli.pipeline", lambda **k: None)
        monkeypatch.setattr(
            sys,
            "argv",
            ["photobook-generator", "generate", "./photos", "--manifest", *manifest_args],
        )

        main()

        assert captured["path"] == expected

    def test_runs_pipeline_per_ingested_album_and_lists_books(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patch_manifest(
            monkeypatch,
            [self._outcome("a", "lisbon-2026"), self._outcome("b", "wedding")],
        )
        slugs: list[str] = []
        monkeypatch.setattr("album.cli.pipeline", lambda **k: slugs.append(k["album_slug"]))
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "generate", "./photos", "--manifest"]
        )

        main()

        assert slugs == ["lisbon-2026", "wedding"]  # one pipeline run per ingested slug
        out = capsys.readouterr().out
        assert str(Path("data/albums/lisbon-2026/book/index.html")) in out
        assert str(Path("data/albums/wedding/book/index.html")) in out

    def test_ingest_failure_is_reported_alongside_a_successful_sibling(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patch_manifest(
            monkeypatch,
            [self._outcome("a", "good"), self._outcome("bad-dir", None, "no images found")],
        )
        monkeypatch.setattr("album.cli.pipeline", lambda **k: None)
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "generate", "./photos", "--manifest"]
        )

        main()  # partial success → no SystemExit

        captured = capsys.readouterr()
        assert str(Path("data/albums/good/book/index.html")) in captured.out
        assert "bad-dir" in captured.err and "no images found" in captured.err

    def test_a_pipeline_failure_is_isolated_and_partial_success_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patch_manifest(
            monkeypatch,
            [self._outcome("a", "boom"), self._outcome("b", "fine")],
        )

        def _pipeline(**kwargs: object) -> None:
            if kwargs["album_slug"] == "boom":
                raise ValueError("pipeline blew up")

        monkeypatch.setattr("album.cli.pipeline", _pipeline)
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "generate", "./photos", "--manifest"]
        )

        main()  # one book still produced → exit zero

        captured = capsys.readouterr()
        assert str(Path("data/albums/fine/book/index.html")) in captured.out
        assert "pipeline blew up" in captured.err

    def test_total_failure_exits_nonzero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._patch_manifest(
            monkeypatch,
            [
                self._outcome("a", None, "no images found"),
                self._outcome("b", None, "no images found"),
            ],
        )
        monkeypatch.setattr("album.cli.pipeline", lambda **k: None)
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "generate", "./photos", "--manifest"]
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1  # every album failed
        assert "0 book(s) written" in capsys.readouterr().out

    def test_malformed_manifest_fails_fast(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _raise(_path: Path) -> Manifest:
            raise ValueError("bad manifest")

        monkeypatch.setattr("album.cli.load_manifest", _raise)
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "generate", "./photos", "--manifest"]
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "bad manifest" in capsys.readouterr().err

    def test_bad_captioner_env_fails_fast_before_ingesting(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A misconfigured CAPTIONER env must fail cleanly *before* any album is
        # uploaded — the captioner is resolved up front, inside the error guard.
        monkeypatch.setattr(
            "album.cli.get_settings",
            lambda: SimpleNamespace(captioner="nonsense"),
        )
        reached: list[str] = []
        monkeypatch.setattr("album.cli.load_manifest", lambda _p: reached.append("load"))
        monkeypatch.setattr("album.cli.ingest_manifest", lambda *a, **k: reached.append("ingest"))
        monkeypatch.setattr(
            sys, "argv", ["photobook-generator", "generate", "./photos", "--manifest"]
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert reached == []  # never loaded the manifest or uploaded anything
