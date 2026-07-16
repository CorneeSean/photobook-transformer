"""Tests for :class:`~album.flow.stages.render.RenderStage` (build step 11).

darktable-cli itself isn't meaningfully unit-testable (working-agreement.md) —
what *is* testable, and covered here, is the orchestration around it: fetching
the original via the injected :class:`OriginalSource`, invoking the injected
:class:`Renderer` with the right ``(input_path, xmp_path, output_path)``
arguments, idempotency, the "not yet enhanced" distinct-count path, per-photo
failure isolation, and the staged-original lifecycle. See render.md /
build-order.md step 11 for the deferred manual verification (a real
``darktable-cli`` binary lives only in the Docker app image, not on this host).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.flow.stages.render import RenderResult, RenderStage


class FakeOriginals:
    """Serves canned original-image bytes keyed by asset id."""

    def __init__(self, by_asset: dict[str, bytes], default: bytes = b"orig-bytes") -> None:
        self._by_asset = by_asset
        self._default = default
        self.calls: list[str] = []

    def get_original(self, asset_id: str) -> bytes:
        self.calls.append(asset_id)
        return self._by_asset.get(asset_id, self._default)


class FakeRenderer:
    """Records invocations instead of shelling out to darktable-cli.

    Writes a stub file at ``output_path`` on a non-raising call, mirroring a
    real darktable-cli invocation actually producing its output — this is what
    the stage's own existence check verifies.
    """

    def __init__(self, *, raise_for: set[Path] | None = None) -> None:
        self._raise_for = raise_for or set()
        self.calls: list[tuple[Path, Path, Path]] = []
        self.input_bytes_seen: list[bytes | None] = []

    def render(self, input_path: Path, xmp_path: Path, output_path: Path) -> None:
        self.calls.append((input_path, xmp_path, output_path))
        # Captured now, before the stage deletes the staged input on success.
        self.input_bytes_seen.append(input_path.read_bytes() if input_path.exists() else None)
        if input_path in self._raise_for:
            raise RuntimeError("darktable-cli exited with a non-zero status")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"rendered-jpeg-bytes")


class TestRenderStage:
    """`RenderStage.run`: argument construction, idempotency, isolation, staging."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def _make_xmp(self, tmp_path: Path, album_slug: str, filename: str) -> Path:
        xmp_path = tmp_path / album_slug / "xmp" / f"{filename}.xmp"
        xmp_path.parent.mkdir(parents=True, exist_ok=True)
        xmp_path.write_text("<xmp/>")
        return xmp_path

    def test_invokes_renderer_with_correct_paths(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")
        originals = FakeOriginals({"a1": b"raw-bytes"})
        renderer = FakeRenderer()

        result = RenderStage(originals, renderer, data_root=tmp_path).run(album_id, session)

        assert isinstance(result, RenderResult)
        assert result.rendered == 1
        assert len(renderer.calls) == 1
        input_path, xmp_path, output_path = renderer.calls[0]
        assert input_path == tmp_path / "test-album" / "originals" / "a1.jpg"
        assert xmp_path == tmp_path / "test-album" / "xmp" / "a1.jpg.xmp"
        assert output_path == tmp_path / "test-album" / "rendered" / "a1.jpg"
        assert renderer.input_bytes_seen == [b"raw-bytes"]
        assert originals.calls == ["a1"]

    def test_sets_rendered_path_on_success(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")

        RenderStage(FakeOriginals({}), FakeRenderer(), data_root=tmp_path).run(album_id, session)

        photo = session.scalars(select(Photo)).one()
        assert photo.rendered_path == str(tmp_path / "test-album" / "rendered" / "a1.jpg")

    def test_skips_photo_with_rendered_path_already_set(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album,
                immich_asset_id="a1",
                filename="a1.jpg",
                selected=True,
                xmp_written=True,
                rendered_path="already/rendered.jpg",
            ),
        ]
        album_id = self._persist(session, album)
        renderer = FakeRenderer()

        result = RenderStage(FakeOriginals({}), renderer, data_root=tmp_path).run(album_id, session)

        assert result == RenderResult(skipped=1)
        assert renderer.calls == []

    def test_force_reprocesses_already_rendered_photo(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album,
                immich_asset_id="a1",
                filename="a1.jpg",
                selected=True,
                xmp_written=True,
                rendered_path="already/rendered.jpg",
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")
        renderer = FakeRenderer()

        result = RenderStage(FakeOriginals({}), renderer, data_root=tmp_path).run(
            album_id, session, force=True
        )

        assert result == RenderResult(rendered=1)
        assert len(renderer.calls) == 1

    def test_flags_selected_photo_without_xmp_instead_of_dropping_it(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=False
            ),
        ]
        album_id = self._persist(session, album)
        renderer = FakeRenderer()

        result = RenderStage(FakeOriginals({}), renderer, data_root=tmp_path).run(album_id, session)

        assert result.not_ready == 1
        assert result.rendered == 0
        assert result.failed == 0
        assert renderer.calls == []
        photo = session.scalars(select(Photo)).one()
        assert photo.rendered_path is None

    def test_skips_unselected_photos(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=False, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        renderer = FakeRenderer()

        result = RenderStage(FakeOriginals({}), renderer, data_root=tmp_path).run(album_id, session)

        assert result == RenderResult()
        assert renderer.calls == []

    def test_isolates_per_photo_failure_from_raising_renderer(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="good", filename="good.jpg", selected=True, xmp_written=True
            ),
            make_photo(
                album, immich_asset_id="bad", filename="bad.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "good.jpg")
        self._make_xmp(tmp_path, "test-album", "bad.jpg")
        bad_input = tmp_path / "test-album" / "originals" / "bad.jpg"
        renderer = FakeRenderer(raise_for={bad_input})

        result = RenderStage(FakeOriginals({}), renderer, data_root=tmp_path).run(album_id, session)

        assert result.rendered == 1
        assert result.failed == 1
        photos = {p.immich_asset_id: p for p in session.scalars(select(Photo))}
        assert photos["good"].rendered_path is not None
        assert photos["bad"].rendered_path is None

    def test_isolates_failure_when_original_fetch_raises(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        class RaisingOriginals:
            def get_original(self, asset_id: str) -> bytes:
                raise RuntimeError("immich unreachable")

        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")

        result = RenderStage(RaisingOriginals(), FakeRenderer(), data_root=tmp_path).run(
            album_id, session
        )

        assert result == RenderResult(failed=1)
        photo = session.scalars(select(Photo)).one()
        assert photo.rendered_path is None

    def test_fails_photo_when_renderer_reports_success_but_no_output_written(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # A Renderer that doesn't raise but also doesn't produce output_path must
        # not be treated as a successful render — a crashed/killed darktable-cli
        # process could in principle exit 0 without writing the file.
        class SilentNoOpRenderer:
            def render(self, input_path: Path, xmp_path: Path, output_path: Path) -> None:
                pass  # deliberately doesn't write output_path

        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")

        result = RenderStage(FakeOriginals({}), SilentNoOpRenderer(), data_root=tmp_path).run(
            album_id, session
        )

        assert result == RenderResult(failed=1)
        photo = session.scalars(select(Photo)).one()
        assert photo.rendered_path is None

    def test_deletes_staged_original_after_successful_render(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")
        input_path = tmp_path / "test-album" / "originals" / "a1.jpg"
        originals = FakeOriginals({"a1": b"raw-bytes"})

        RenderStage(originals, FakeRenderer(), data_root=tmp_path).run(album_id, session)

        # The staged copy was only scratch space for darktable-cli's input; the
        # rendered JPEG is the artifact worth keeping on disk.
        assert not input_path.exists()

    def test_reuses_already_staged_original_without_refetching(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # A retry after a prior failed attempt shouldn't re-download the original
        # from Immich if it's already staged on disk.
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")
        input_path = tmp_path / "test-album" / "originals" / "a1.jpg"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(b"already-staged-bytes")
        originals = FakeOriginals({"a1": b"fresh-bytes-should-not-be-used"})

        RenderStage(originals, FakeRenderer(), data_root=tmp_path).run(album_id, session)

        assert originals.calls == []  # get_original never called

    def test_leaves_staged_original_in_place_after_a_failed_render(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # A failed render's staged original is left alone so a retry doesn't need
        # to re-fetch it.
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album, immich_asset_id="a1", filename="a1.jpg", selected=True, xmp_written=True
            ),
        ]
        album_id = self._persist(session, album)
        self._make_xmp(tmp_path, "test-album", "a1.jpg")
        input_path = tmp_path / "test-album" / "originals" / "a1.jpg"
        renderer = FakeRenderer(raise_for={input_path})

        RenderStage(FakeOriginals({"a1": b"raw-bytes"}), renderer, data_root=tmp_path).run(
            album_id, session
        )

        assert input_path.exists()

    def test_scoped_to_album(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        other = make_album(slug="other")
        other.photos = [
            make_photo(
                other, immich_asset_id="o1", filename="o1.jpg", selected=True, xmp_written=True
            )
        ]
        other_id = self._persist(session, other)
        target = make_album(slug="target")
        target.photos = [
            make_photo(
                target, immich_asset_id="t1", filename="t1.jpg", selected=True, xmp_written=True
            )
        ]
        target_id = self._persist(session, target)
        self._make_xmp(tmp_path, "target", "t1.jpg")

        RenderStage(FakeOriginals({}), FakeRenderer(), data_root=tmp_path).run(target_id, session)

        other_photo = session.scalars(select(Photo).where(Photo.album_id == other_id)).one()
        assert other_photo.rendered_path is None
        target_photo = session.scalars(select(Photo).where(Photo.album_id == target_id)).one()
        assert target_photo.rendered_path is not None

    def test_raises_for_unknown_album(self, session: Session, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            RenderStage(FakeOriginals({}), FakeRenderer(), data_root=tmp_path).run(999, session)
