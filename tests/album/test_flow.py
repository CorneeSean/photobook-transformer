"""Tests for ``flow.py`` (build steps 14 + 25): the album-bootstrap
idempotency and the pipeline's DAG wiring.

Prefect orchestration itself isn't the thing under test here — each stage
already has its own thorough test suite. What matters, and is covered here:

- :func:`get_or_create_album`'s idempotent bootstrap from ``album.yml``.
- :func:`pipeline` calls every stage exactly once, with the correct
  ``album_id``, respecting the DAG's partial order (build step 25 —
  ``knowledge/foundations/pipeline-execution.md``: four sequential stages,
  two fan-out groups, two join fences) — not a single fixed sequence, since
  each group's three/two members are no longer ordered relative to each
  other.
- A group-1 failure aborts the run before ``select`` (the join fence
  re-raises), rather than silently continuing.
- ``pipeline`` only closes resources it opened itself.

Each stage's ``@task`` wrapper (``_sync_task`` … ``_fulfill_task``) is
monkeypatched at the ``album.flow`` module level — the stages themselves are not
re-exercised for real (that's each stage's own test file's job), and patching the
task wrappers rather than the stage classes keeps these wiring tests decoupled
from how any one stage is constructed. Doubles for the six grouped stages are
wrapped with Prefect's own ``task()`` (not plain lambdas) since ``pipeline``
now calls ``.submit()`` on them; sequential-stage doubles work either way
since calling a Prefect ``Task`` directly just runs it. This runs fast and
never touches Postgres, Immich, Claude, darktable-cli, or Lulu. Runs against
Prefect's ``prefect_test_harness`` (a temporary local SQLite Prefect
database) and its default ``ThreadPoolTaskRunner``, not a real Prefect
server or ``~/.prefect``.

Real concurrent per-task sessions (the thing an in-memory SQLite session
can't exercise — it isn't shareable across threads) are covered separately
by ``tests/album/test_flow_integration.py`` against a Postgres+pgvector
testcontainer.

**Manual verification** (build-order.md step 14: "`album run --album
honeymoon-2026` runs end-to-end; the Prefect UI shows it") needs a reachable
Immich instance with real photos, a real `ANTHROPIC_API_KEY`, real
`darktable-cli` (Docker app image), and real Lulu credentials — none
available in this environment. Deferred, same pattern as every other
live-integration check in this project (see knowledge/build/progress.md).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from prefect import task
from prefect.testing.utilities import prefect_test_harness
from pydantic_ai.models.test import TestModel
from sqlalchemy.orm import Session

import album.flow as flow_module
from album.db.models import Album
from album.flow import get_or_create_album, pipeline
from album.printers.html import HtmlPrinter

# The ten stages, and the flow-module attribute of each one's @task wrapper.
# Patching these decouples the wiring tests from stage construction. Order
# here is just enumeration order — build step 25 means only a *partial* order
# is guaranteed at runtime (see _GROUP_1/_GROUP_2 below).
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

# Group 1 (after sync, joins at select) and group 2 (after select, joins at
# assemble) — the six stages `pipeline` runs via `.submit()`/`.result()`
# rather than a direct call (pipeline-execution.md §"The change").
_GROUP_1: tuple[str, ...] = ("resolve_leads", "quality", "cluster")
_GROUP_2: tuple[str, ...] = ("enhance", "render", "caption")


@pytest.fixture(scope="module", autouse=True)
def _prefect_test_db() -> Iterator[None]:
    """Run every test in this file against a temporary, isolated Prefect
    database instead of a real server or the developer's ``~/.prefect``."""
    with prefect_test_harness():
        yield


def _task_attr(stage_name: str) -> str:
    return f"_{stage_name}_task"


def _patch_stage_tasks_noop(monkeypatch: pytest.MonkeyPatch, *, exclude: str = "") -> None:
    """No-op double for every stage's @task wrapper except ``exclude``. Every
    double is Prefect-``task()``-wrapped (not a plain lambda) so ``.submit()``
    — which ``pipeline`` calls on the six grouped stages — still resolves."""
    for stage_name in _STAGE_NAMES:
        if stage_name == exclude:
            continue
        monkeypatch.setattr(
            flow_module, _task_attr(stage_name), task(name=stage_name)(lambda *a, **k: None)
        )


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

    def _record(
        self, name: str, album_id: int, calls: list[str], lock: threading.Lock
    ) -> Callable[..., None]:
        def _fn(received_album_id: int, *_args: object, **_kwargs: object) -> None:
            assert received_album_id == album_id
            with lock:
                calls.append(name)

        return _fn

    def test_runs_every_stage_once_respecting_the_dag_partial_order(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        """Exact sequence is no longer deterministic within a group (build step
        25), so this asserts position/precedence rather than a fixed list."""
        album_id = self._persist(
            session, make_album(slug="honeymoon-2026", immich_album_id="immich-1")
        )

        calls: list[str] = []
        lock = threading.Lock()

        for stage_name in _STAGE_NAMES:
            monkeypatch.setattr(
                flow_module,
                _task_attr(stage_name),
                task(name=stage_name)(self._record(stage_name, album_id, calls, lock)),
            )

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

        assert sorted(calls) == sorted(_STAGE_NAMES)  # every stage ran exactly once
        idx = {name: calls.index(name) for name in calls}

        assert idx["sync"] == 0
        assert idx["fulfill"] == len(calls) - 1
        for name in _GROUP_1:
            assert idx["sync"] < idx[name] < idx["select"]
        for name in _GROUP_2:
            assert idx["select"] < idx[name] < idx["assemble"]
        assert idx["render"] > idx["enhance"]  # the wait_for chain
        assert idx["caption"] > idx["enhance"]  # caption is also chained behind enhance
        assert idx["assemble"] < idx["fulfill"]

    def test_grouped_tasks_get_the_session_factory_sequential_tasks_get_the_shared_session(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        """`pipeline`'s six grouped tasks take a `session_factory`, never the
        flow's shared session — the whole point of build step 25 (a shared
        SQLAlchemy Session isn't thread-safe)."""
        self._persist(session, make_album(slug="honeymoon-2026", immich_album_id="immich-1"))

        received: dict[str, object] = {}
        lock = threading.Lock()

        def _capture(name: str) -> Callable[..., None]:
            def _fn(
                _album_id: int, second_arg: object = None, *_args: object, **_kwargs: object
            ) -> None:
                with lock:
                    received[name] = second_arg

            return _fn

        for stage_name in _STAGE_NAMES:
            monkeypatch.setattr(
                flow_module, _task_attr(stage_name), task(name=stage_name)(_capture(stage_name))
            )

        def fake_factory() -> Session:  # never actually called — the double doesn't open it
            raise AssertionError("the double shouldn't call the factory")

        pipeline(
            album_slug="honeymoon-2026",
            session=session,
            session_factory=fake_factory,
            immich_client=_FakeImmichClient(),  # pyright: ignore[reportArgumentType]
            similarity=object(),  # pyright: ignore[reportArgumentType]
            aesthetic=object(),  # pyright: ignore[reportArgumentType]
            embedder=object(),  # pyright: ignore[reportArgumentType]
            renderer=object(),  # pyright: ignore[reportArgumentType]
            printer=object(),  # pyright: ignore[reportArgumentType]
            model=TestModel(),
        )

        for name in (*_GROUP_1, *_GROUP_2):
            assert received[name] is fake_factory
        for name in ("sync", "select", "assemble", "fulfill"):
            assert received[name] is session

    def test_group_1_failure_aborts_before_select(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        """A group-1 task raising propagates out of `pipeline` (the `.result()`
        join re-raises it) and nothing downstream of the join runs."""
        album_id = self._persist(
            session, make_album(slug="honeymoon-2026", immich_album_id="immich-1")
        )

        calls: list[str] = []
        lock = threading.Lock()

        def _raising_quality(*_args: object, **_kwargs: object) -> None:
            with lock:
                calls.append("quality")
            raise RuntimeError("boom: quality failed")

        for stage_name in _STAGE_NAMES:
            if stage_name == "quality":
                monkeypatch.setattr(
                    flow_module, _task_attr(stage_name), task(name=stage_name)(_raising_quality)
                )
            else:
                monkeypatch.setattr(
                    flow_module,
                    _task_attr(stage_name),
                    task(name=stage_name)(self._record(stage_name, album_id, calls, lock)),
                )

        with pytest.raises(RuntimeError, match="boom: quality failed"):
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

        assert "quality" in calls
        # resolve_leads is .result()-ed (in _run_group_1's submission order)
        # before quality's raise is reached, so it's deterministically done by
        # then — proof the other group members aren't poisoned by quality's
        # failure, not just that the join eventually re-raises.
        assert "resolve_leads" in calls
        for name in ("select", *_GROUP_2, "assemble", "fulfill"):
            assert name not in calls

    def test_enhance_failure_surfaces_directly_not_as_a_masked_upstream_error(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_album: Callable[..., Album],
    ) -> None:
        """`_run_group_2` joins `enhance_future` explicitly, before render's/
        caption's own joins, precisely so a real `enhance` failure raises its
        own exception rather than the `UpstreamTaskError` `render`'s/
        `caption`'s `wait_for`-blocked `.result()` would otherwise raise."""
        album_id = self._persist(
            session, make_album(slug="honeymoon-2026", immich_album_id="immich-1")
        )

        calls: list[str] = []
        lock = threading.Lock()

        def _raising_enhance(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom: enhance failed")

        for stage_name in _STAGE_NAMES:
            if stage_name == "enhance":
                monkeypatch.setattr(
                    flow_module, _task_attr(stage_name), task(name=stage_name)(_raising_enhance)
                )
            else:
                monkeypatch.setattr(
                    flow_module,
                    _task_attr(stage_name),
                    task(name=stage_name)(self._record(stage_name, album_id, calls, lock)),
                )

        with pytest.raises(RuntimeError, match="boom: enhance failed"):
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

        for name in ("render", "caption", "assemble", "fulfill"):
            assert name not in calls

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
