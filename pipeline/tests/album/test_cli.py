"""Tests for ``cli.py``'s single ``build`` command (step 39 — unified from the old
``generate``/``run`` pair).

``pipeline()``, ``ingest_directory()`` and ``ingest_manifest()`` are exercised in
their own modules — this file only checks that ``main()`` picks the right
:class:`~album.cli.sources.AlbumSource` strategy from the parsed args, threads options
through, turns expected user-fixable errors (``FileNotFoundError``/``ValueError``/
``IngestError``) into a clean message + exit 1 (letting anything else propagate),
and rejects argument misuse with argparse's exit-2 usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from album.analysis.captioner import ClaudeCaptioner, TemplateCaptioner
from album.cli import main
from album.flow.stages.base import StageTotalFailure
from album.ingest import AlbumOutcome, IngestError
from album.manifest import AlbumSpec, Manifest


@pytest.fixture(autouse=True)
def _default_captioner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``get_settings`` so ``--captioner``-less runs resolve the default without
    needing a real ``.env`` (``DATABASE_URL`` etc.). Individual tests override it."""
    monkeypatch.setattr(
        "album.cli.runner.get_settings", lambda: SimpleNamespace(captioner="template")
    )


def _argv(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["photobook-generator", "build", *args])


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("album.cli.runner.pipeline", lambda **kwargs: calls.append(kwargs))
    return calls


class TestBuildSingle:
    """`build <dir>`: ingest one directory → one Immich album → run → one book."""

    def _stub_ingest(
        self, monkeypatch: pytest.MonkeyPatch, *, returns: str = "lisbon-trip"
    ) -> list[tuple[tuple[object, ...], dict[str, object]]]:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def _ingest(*args: object, **kwargs: object) -> str:
            calls.append((args, kwargs))
            return returns

        monkeypatch.setattr("album.cli.sources.ingest_directory", _ingest)
        return calls

    def test_ingests_then_runs_pipeline_and_prints_book_path(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ingest_calls = self._stub_ingest(monkeypatch, returns="lisbon-trip")
        pipeline_calls = _stub_pipeline(monkeypatch)
        _argv(monkeypatch, "./photos")

        main()

        # The directory was passed as a Path; the returned slug drove the pipeline.
        assert ingest_calls[0][0] == (Path("./photos"),)
        assert [c["album_slug"] for c in pipeline_calls] == ["lisbon-trip"]
        assert str(Path("data/albums/lisbon-trip/book/index.html")) in capsys.readouterr().out

    def test_passes_all_ingest_options_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ingest_calls = self._stub_ingest(monkeypatch)
        _stub_pipeline(monkeypatch)
        _argv(
            monkeypatch,
            ".",
            "--slug",
            "trip-2026",
            "--title",
            "Our Trip",
            "--timezone",
            "Europe/Warsaw",
            "--source-document",
            "schedule.pdf",
            "--ingest-mode",
            "external",
            "--force",
        )

        main()

        assert ingest_calls[0][1] == {
            "slug": "trip-2026",
            "title": "Our Trip",
            "timezone": "Europe/Warsaw",
            "source_document": Path("schedule.pdf"),
            "force": True,
            "lead_face_images": None,
            "ingest_mode": "external",
        }

    def test_lead_face_flag_is_repeatable_and_threaded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ingest_calls = self._stub_ingest(monkeypatch)
        _stub_pipeline(monkeypatch)
        _argv(monkeypatch, "./photos", "--lead-face", "alex.jpg", "--lead-face", "sam.jpg")

        main()

        assert ingest_calls[0][1]["lead_face_images"] == [Path("alex.jpg"), Path("sam.jpg")]

    def test_no_lead_face_flag_passes_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ingest_calls = self._stub_ingest(monkeypatch)
        _stub_pipeline(monkeypatch)
        _argv(monkeypatch, "./photos")

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
    def test_expected_ingest_errors_are_clean_and_exit_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
    ) -> None:
        def _raise(*args: object, **kwargs: object) -> str:
            raise error

        monkeypatch.setattr("album.cli.sources.ingest_directory", _raise)
        _stub_pipeline(monkeypatch)
        _argv(monkeypatch, "./photos")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert str(error) in capsys.readouterr().err

    def test_unexpected_ingest_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*args: object, **kwargs: object) -> str:
            raise RuntimeError("something genuinely unexpected")

        monkeypatch.setattr("album.cli.sources.ingest_directory", _raise)
        _argv(monkeypatch, "./photos")

        with pytest.raises(RuntimeError, match="something genuinely unexpected"):
            main()


class TestBuildNoIngest:
    """`build --no-ingest --album SLUG`: skip ingest, run an existing album.yml."""

    def test_runs_pipeline_for_the_existing_album(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pipeline_calls = _stub_pipeline(monkeypatch)
        _argv(monkeypatch, "--no-ingest", "--album", "honeymoon-2026")

        main()

        assert [c["album_slug"] for c in pipeline_calls] == ["honeymoon-2026"]
        assert str(Path("data/albums/honeymoon-2026/book/index.html")) in capsys.readouterr().out

    def test_lead_face_patches_album_yml_before_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []
        patch_calls: list[tuple[Path, list[Path]]] = []

        def _patch(album_yml: Path, images: list[Path]) -> None:
            patch_calls.append((album_yml, images))
            order.append("patch")

        monkeypatch.setattr("album.cli.sources.patch_lead_faces", _patch)
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: order.append("pipeline"))
        _argv(monkeypatch, "--no-ingest", "--album", "trip", "--lead-face", "alex.jpg")

        main()

        assert order == ["patch", "pipeline"]  # leads patched before the pipeline runs
        assert patch_calls == [(Path("data/albums/trip/album.yml"), [Path("alex.jpg")])]

    def test_without_lead_face_does_not_patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patched: list[object] = []
        monkeypatch.setattr("album.cli.sources.patch_lead_faces", lambda *a: patched.append(a))
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: None)
        _argv(monkeypatch, "--no-ingest", "--album", "trip")

        main()

        assert patched == []

    def test_lead_face_patch_error_is_clean_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _raise(album_yml: Path, images: list[Path]) -> None:
            raise IngestError("no album.yml at data/albums/trip/album.yml — run build first")

        monkeypatch.setattr("album.cli.sources.patch_lead_faces", _raise)
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: None)
        _argv(monkeypatch, "--no-ingest", "--album", "trip", "--lead-face", "alex.jpg")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "no album.yml" in capsys.readouterr().err


class TestArgValidation:
    """Argument misuse is an argparse usage error (exit 2), before any work runs."""

    def test_missing_directory_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _argv(monkeypatch)  # no directory, no --no-ingest

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2

    def test_album_without_no_ingest_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _argv(monkeypatch, "./photos", "--album", "trip")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2

    def test_no_ingest_without_album_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _argv(monkeypatch, "--no-ingest")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2

    @pytest.mark.parametrize(
        "flag",
        [["--manifest"], ["--slug", "x"], ["--timezone", "UTC"], ["--ingest-mode", "external"]],
        ids=["manifest", "slug", "timezone", "ingest-mode"],
    )
    def test_no_ingest_rejects_ingest_options(
        self, monkeypatch: pytest.MonkeyPatch, flag: list[str]
    ) -> None:
        _argv(monkeypatch, "--no-ingest", "--album", "trip", *flag)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2

    def test_no_ingest_directory_positional_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _argv(monkeypatch, "./photos", "--no-ingest", "--album", "trip")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2


class TestBuildManifest:
    """`build <root> --manifest`: fan a tree into N albums → N books."""

    def _outcome(self, path: str, slug: str | None, error: str | None = None) -> AlbumOutcome:
        return AlbumOutcome(AlbumSpec(path=path), slug, error)

    def _patch_manifest(
        self, monkeypatch: pytest.MonkeyPatch, outcomes: list[AlbumOutcome]
    ) -> None:
        """Patch load_manifest + ingest_manifest so no disk/Immich is touched."""
        monkeypatch.setattr(
            "album.cli.sources.load_manifest", lambda _p: Manifest(albums=[AlbumSpec(path="a")])
        )
        monkeypatch.setattr("album.cli.sources.ingest_manifest", lambda *a, **k: outcomes)

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
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], flag: list[str]
    ) -> None:
        called: list[int] = []
        monkeypatch.setattr(
            "album.cli.sources.ingest_manifest", lambda *a, **k: called.append(1) or []
        )
        _argv(monkeypatch, "./photos", "--manifest", *flag)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 2  # argparse usage error
        assert "per-album" in capsys.readouterr().err
        assert called == []  # rejected before the fan-out ran

    def test_ingest_mode_is_run_level_and_threaded_into_the_fan_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "album.cli.sources.load_manifest", lambda _p: Manifest(albums=[AlbumSpec(path="a")])
        )
        monkeypatch.setattr(
            "album.cli.sources.ingest_manifest",
            lambda *a, **k: captured.update(k) or [self._outcome("a", "a")],
        )
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: None)
        _argv(monkeypatch, "./photos", "--manifest", "--ingest-mode", "external")

        main()

        assert captured["ingest_mode"] == "external"

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
        self, monkeypatch: pytest.MonkeyPatch, manifest_args: list[str], expected: Path
    ) -> None:
        captured: dict[str, Path] = {}

        def _load(path: Path) -> Manifest:
            captured["path"] = path
            return Manifest(albums=[AlbumSpec(path="a")])

        monkeypatch.setattr("album.cli.sources.load_manifest", _load)
        monkeypatch.setattr(
            "album.cli.sources.ingest_manifest", lambda *a, **k: [self._outcome("a", "a")]
        )
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: None)
        _argv(monkeypatch, "./photos", "--manifest", *manifest_args)

        main()

        assert captured["path"] == expected

    def test_runs_pipeline_per_ingested_album_and_lists_books(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._patch_manifest(
            monkeypatch, [self._outcome("a", "lisbon-2026"), self._outcome("b", "wedding")]
        )
        slugs: list[str] = []
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: slugs.append(k["album_slug"]))
        _argv(monkeypatch, "./photos", "--manifest")

        main()

        assert slugs == ["lisbon-2026", "wedding"]  # one pipeline run per ingested slug
        out = capsys.readouterr().out
        assert str(Path("data/albums/lisbon-2026/book/index.html")) in out
        assert str(Path("data/albums/wedding/book/index.html")) in out

    def test_ingest_failure_is_reported_alongside_a_successful_sibling(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._patch_manifest(
            monkeypatch,
            [self._outcome("a", "good"), self._outcome("bad-dir", None, "no images found")],
        )
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: None)
        _argv(monkeypatch, "./photos", "--manifest")

        main()  # partial success → no SystemExit

        captured = capsys.readouterr()
        assert str(Path("data/albums/good/book/index.html")) in captured.out
        assert "bad-dir" in captured.err and "no images found" in captured.err

    def test_a_pipeline_failure_is_isolated_and_partial_success_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._patch_manifest(monkeypatch, [self._outcome("a", "boom"), self._outcome("b", "fine")])

        def _pipeline(**kwargs: object) -> None:
            if kwargs["album_slug"] == "boom":
                raise ValueError("pipeline blew up")

        monkeypatch.setattr("album.cli.runner.pipeline", _pipeline)
        _argv(monkeypatch, "./photos", "--manifest")

        main()  # one book still produced → exit zero

        captured = capsys.readouterr()
        assert str(Path("data/albums/fine/book/index.html")) in captured.out
        assert "pipeline blew up" in captured.err

    def test_stage_total_failure_is_isolated_per_album(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A systemic stage failure (every attempted photo failed) escalates as a
        # StageTotalFailure; run_pipelines must isolate it per album so a healthy
        # sibling still builds, rather than crashing the whole fan-out (step 41 review).
        self._patch_manifest(monkeypatch, [self._outcome("a", "down"), self._outcome("b", "fine")])

        def _pipeline(**kwargs: object) -> None:
            if kwargs["album_slug"] == "down":
                raise StageTotalFailure("quality: failed for all 120 attempted photo(s)")

        monkeypatch.setattr("album.cli.runner.pipeline", _pipeline)
        _argv(monkeypatch, "./photos", "--manifest")

        main()  # sibling produced a book → exit zero

        captured = capsys.readouterr()
        assert str(Path("data/albums/fine/book/index.html")) in captured.out
        assert "failed for all 120 attempted" in captured.err

    def test_total_failure_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._patch_manifest(
            monkeypatch,
            [
                self._outcome("a", None, "no images found"),
                self._outcome("b", None, "no images found"),
            ],
        )
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **k: None)
        _argv(monkeypatch, "./photos", "--manifest")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1  # every album failed
        assert "0 book(s) written" in capsys.readouterr().out

    def test_malformed_manifest_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _raise(_path: Path) -> Manifest:
            raise ValueError("bad manifest")

        monkeypatch.setattr("album.cli.sources.load_manifest", _raise)
        _argv(monkeypatch, "./photos", "--manifest")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "bad manifest" in capsys.readouterr().err

    def test_bad_captioner_env_fails_fast_before_ingesting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A misconfigured CAPTIONER env must fail cleanly *before* any album is
        # uploaded — the captioner is resolved up front, inside the error guard.
        monkeypatch.setattr(
            "album.cli.runner.get_settings", lambda: SimpleNamespace(captioner="nonsense")
        )
        reached: list[str] = []
        monkeypatch.setattr("album.cli.sources.load_manifest", lambda _p: reached.append("load"))
        monkeypatch.setattr(
            "album.cli.sources.ingest_manifest", lambda *a, **k: reached.append("ingest")
        )
        _argv(monkeypatch, "./photos", "--manifest")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert reached == []  # never loaded the manifest or uploaded anything


class TestCaptionerSelection:
    """`--captioner` (per-run) overrides the `CAPTIONER` env default, and both are
    threaded into `pipeline` alongside `--force` — the A/B enabler."""

    def _capture_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        captured: dict[str, object] = {}
        monkeypatch.setattr("album.cli.runner.pipeline", lambda **kwargs: captured.update(kwargs))
        return captured

    @pytest.mark.parametrize("no_ingest", [False, True], ids=["ingest", "no-ingest"])
    def test_flag_threaded_into_pipeline(
        self, monkeypatch: pytest.MonkeyPatch, no_ingest: bool
    ) -> None:
        captured = self._capture_pipeline(monkeypatch)
        monkeypatch.setattr("album.cli.sources.ingest_directory", lambda *a, **k: "slug")
        args = (
            ["--no-ingest", "--album", "a", "--captioner", "claude", "--force"]
            if no_ingest
            else ["./photos", "--captioner", "claude", "--force"]
        )
        _argv(monkeypatch, *args)

        main()

        assert isinstance(captured["captioner"], ClaudeCaptioner)
        assert captured["force"] is True

    def test_flag_overrides_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "album.cli.runner.get_settings", lambda: SimpleNamespace(captioner="template")
        )
        captured = self._capture_pipeline(monkeypatch)
        _argv(monkeypatch, "--no-ingest", "--album", "a", "--captioner", "claude")

        main()

        assert isinstance(captured["captioner"], ClaudeCaptioner)  # flag wins over env
        assert captured["force"] is False

    def test_env_default_used_without_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "album.cli.runner.get_settings", lambda: SimpleNamespace(captioner="template")
        )
        captured = self._capture_pipeline(monkeypatch)
        _argv(monkeypatch, "--no-ingest", "--album", "a")

        main()

        assert isinstance(captured["captioner"], TemplateCaptioner)
