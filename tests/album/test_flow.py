"""Tests for ``flow.py`` (build step 14): the album-bootstrap idempotency and
the pipeline's wiring/ordering.

Prefect orchestration itself isn't the thing under test here — each stage
already has its own thorough test suite. What matters, and is covered here:

- :func:`get_or_create_album`'s idempotent bootstrap from ``album.yml``.
- :func:`pipeline` calls every stage exactly once, in build order, with the
  correct ``album_id``, and only closes resources it opened itself.

Each stage's ``@task`` wrapper (``_sync_task`` … ``_fulfill_task``) is
monkeypatched at the ``album.flow`` module level — the stages themselves are not
re-exercised for real (that's each stage's own test file's job), and patching the
task wrappers rather than the stage classes keeps these wiring tests decoupled
from how any one stage is constructed. This runs fast and never touches Postgres,
Immich, Claude, darktable-cli, or Lulu. Runs against Prefect's
``prefect_test_harness`` (a temporary local SQLite Prefect database), not a real
Prefect server or ``~/.prefect``.

**Manual verification** (build-order.md step 14: "`album run --album
honeymoon-2026` runs end-to-end; the Prefect UI shows it") needs a reachable
Immich instance with real photos, a real `ANTHROPIC_API_KEY`, real
`darktable-cli` (Docker app image), and real Lulu credentials — none
available in this environment. Deferred, same pattern as every other
live-integration check in this project (see knowledge/build/progress.md).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from prefect.testing.utilities import prefect_test_harness
from pydantic_ai.models.test import TestModel
from sqlalchemy.orm import Session

import album.flow as flow_module
from album.db.models import Album
from album.flow import get_or_create_album, pipeline
from album.printers.html import HtmlPrinter

# The stages in build order, and the flow-module attribute of each one's @task
# wrapper. Patching these decouples the wiring tests from stage construction.
_STAGE_NAMES: tuple[str, ...] = (
    "sync",
    "resolve_leads",
    "quality",
    "cluster",
    "select",
    "enhance",
    "render",
    "caption",
    "assemble",
    "fulfill",
)


@pytest.fixture(scope="module", autouse=True)
def _prefect_test_db() -> Iterator[None]:
    """Run every test in this file against a temporary, isolated Prefect
    database instead of a real server or the developer's ``~/.prefect``."""
    with prefect_test_harness():
        yield


def _task_attr(stage_name: str) -> str:
    return f"_{stage_name}_task"


def _patch_stage_tasks_noop(monkeypatch: pytest.MonkeyPatch, *, exclude: str = "") -> None:
    for stage_name in _STAGE_NAMES:
        if stage_name == exclude:
            continue
        monkeypatch.setattr(flow_module, _task_attr(stage_name), lambda *a, **k: None)


class _FakeImmichClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestGetOrCreateAlbum:
    """Idempotent album bootstrap from ``<albums_dir>/<slug>/album.yml``."""

    def _write_album_yaml(self, tmp_path: Path, slug: str) -> Path:
        album_dir = tmp_path / slug
        album_dir.mkdir(parents=True)
        (album_dir / "album.yml").write_text(
            f"slug: {slug}\ntitle: Our Honeymoon\nimmich_album_id: immich-abc-123\n"
        )
        return tmp_path

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def test_creates_from_yaml(self, session: Session, tmp_path: Path) -> None:
        albums_dir = self._write_album_yaml(tmp_path, "honeymoon-2026")

        album_id = get_or_create_album(session, "honeymoon-2026", albums_dir)

        album = session.get(Album, album_id)
        assert album is not None
        assert album.slug == "honeymoon-2026"
        assert album.immich_album_id == "immich-abc-123"

    def test_reuses_existing_without_reading_yaml(
        self, session: Session, tmp_path: Path, make_album: Callable[..., Album]
    ) -> None:
        existing_id = self._persist(session, make_album(slug="honeymoon-2026"))
        # No album.yml on disk at all for this slug — proves the existing row is
        # reused without ever trying to parse a file.
        empty_albums_dir = tmp_path / "empty"
        empty_albums_dir.mkdir()

        album_id = get_or_create_album(session, "honeymoon-2026", empty_albums_dir)

        assert album_id == existing_id

    def test_raises_for_missing_yaml(self, session: Session, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="album.yml"):
            get_or_create_album(session, "no-such-album", tmp_path)


class TestPipelineWiring:
    """`pipeline` calls every stage once, in order, with the right album_id, and
    defaults its injectable dependencies to the real (credential-free) things."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def test_runs_every_stage_once_in_order_with_the_right_album_id(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        album_id = self._persist(
            session, make_album(slug="honeymoon-2026", immich_album_id="immich-1")
        )

        calls: list[str] = []

        def _record(name: str) -> Callable[..., None]:
            def _fn(received_album_id: int, *_args: object, **_kwargs: object) -> None:
                assert received_album_id == album_id
                calls.append(name)

            return _fn

        for stage_name in _STAGE_NAMES:
            monkeypatch.setattr(flow_module, _task_attr(stage_name), _record(stage_name))

        pipeline(
            album_slug="honeymoon-2026",
            session=session,
            immich_client=_FakeImmichClient(),  # pyright: ignore[reportArgumentType]
            similarity=object(),  # pyright: ignore[reportArgumentType]
            aesthetic=object(),  # pyright: ignore[reportArgumentType]
            embedder=object(),  # pyright: ignore[reportArgumentType]
            renderer=object(),  # pyright: ignore[reportArgumentType]
            printer=object(),  # pyright: ignore[reportArgumentType]
            model=TestModel(),
        )

        assert calls == list(_STAGE_NAMES)

    def test_does_not_close_an_injected_session_or_client(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        self._persist(session, make_album(slug="honeymoon-2026", immich_album_id="immich-1"))
        _patch_stage_tasks_noop(monkeypatch)
        fake_client = _FakeImmichClient()

        pipeline(
            album_slug="honeymoon-2026",
            session=session,
            immich_client=fake_client,  # pyright: ignore[reportArgumentType]
            similarity=object(),  # pyright: ignore[reportArgumentType]
            aesthetic=object(),  # pyright: ignore[reportArgumentType]
            embedder=object(),  # pyright: ignore[reportArgumentType]
            renderer=object(),  # pyright: ignore[reportArgumentType]
            printer=object(),  # pyright: ignore[reportArgumentType]
            model=TestModel(),
        )

        assert fake_client.closed is False
        # A caller-owned session must still be usable — pipeline() must not have closed it.
        assert session.get(Album, 1) is not None

    def test_builds_and_closes_its_own_client_when_none_is_injected(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        self._persist(session, make_album(slug="honeymoon-2026", immich_album_id="immich-1"))
        _patch_stage_tasks_noop(monkeypatch)

        built = _FakeImmichClient()
        monkeypatch.setattr(
            flow_module.ImmichClient, "from_settings", classmethod(lambda cls: built)
        )

        pipeline(
            album_slug="honeymoon-2026",
            session=session,
            similarity=object(),  # pyright: ignore[reportArgumentType]
            aesthetic=object(),  # pyright: ignore[reportArgumentType]
            embedder=object(),  # pyright: ignore[reportArgumentType]
            renderer=object(),  # pyright: ignore[reportArgumentType]
            printer=object(),  # pyright: ignore[reportArgumentType]
            model=TestModel(),
        )

        assert built.closed is True

    def test_uses_html_printer_by_default_when_none_is_injected(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        """When no printer is injected, the default output medium is the credential-
        free :class:`~album.printers.html.HtmlPrinter` — not Lulu. (Lulu needs live
        credentials + a hosted PDF, so it's opt-in, injected by the caller.)"""
        self._persist(session, make_album(slug="honeymoon-2026", immich_album_id="immich-1"))

        captured: dict[str, object] = {}
        _patch_stage_tasks_noop(monkeypatch, exclude="fulfill")

        def _capture_fulfill(
            album_id: int, session: Session, printer: object, **_kwargs: object
        ) -> None:
            captured["printer"] = printer

        monkeypatch.setattr(flow_module, "_fulfill_task", _capture_fulfill)
        monkeypatch.setattr(
            flow_module.ImmichClient, "from_settings", classmethod(lambda cls: _FakeImmichClient())
        )

        pipeline(
            album_slug="honeymoon-2026",
            session=session,
            similarity=object(),  # pyright: ignore[reportArgumentType]
            aesthetic=object(),  # pyright: ignore[reportArgumentType]
            embedder=object(),  # pyright: ignore[reportArgumentType]
            renderer=object(),  # pyright: ignore[reportArgumentType]
            model=TestModel(),
            # printer deliberately not injected, forcing pipeline() to default it.
        )

        assert isinstance(captured["printer"], HtmlPrinter)


class TestPipelineResourceCleanup:
    """db_session ownership survives a *later* resource (Immich client) failing
    to construct — both live inside the same outer try/finally."""

    def test_does_not_close_injected_session_if_client_construction_fails(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise() -> None:
            raise RuntimeError("Immich unreachable")

        monkeypatch.setattr(
            flow_module.ImmichClient, "from_settings", classmethod(lambda cls: _raise())
        )

        closed = False
        real_close = session.close

        def _tracking_close() -> None:
            nonlocal closed
            closed = True
            real_close()

        monkeypatch.setattr(session, "close", _tracking_close)

        with pytest.raises(RuntimeError, match="Immich unreachable"):
            pipeline(album_slug="honeymoon-2026", session=session)

        assert closed is False  # caller-owned session — pipeline() must not close it either way

    def test_closes_its_own_session_if_client_construction_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The owned (not injected) session is the resource that leaked before
        the fix: client construction happens inside the same try that guards
        db_session's cleanup, and fails before ``get_or_create_album`` runs, so
        no DB access happens — ``new_session()`` is replaced with a fake whose
        ``close()`` is spied on directly."""

        class _FakeSession:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        fake_session = _FakeSession()
        monkeypatch.setattr(flow_module, "new_session", lambda: fake_session)

        def _raise() -> None:
            raise RuntimeError("Immich unreachable")

        monkeypatch.setattr(
            flow_module.ImmichClient, "from_settings", classmethod(lambda cls: _raise())
        )

        with pytest.raises(RuntimeError, match="Immich unreachable"):
            pipeline(album_slug="honeymoon-2026")  # pyright: ignore[reportArgumentType]

        assert fake_session.closed is True
