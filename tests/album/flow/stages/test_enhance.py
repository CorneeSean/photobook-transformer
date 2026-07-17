"""Tests for :class:`~album.flow.stages.enhance.EnhanceStage` (build step 10) and its
pure tone helpers.

Covers, in order: the pure baseline+AI-delta reconciliation math (no DB, no
images); histogram-match baseline offset over small fixture images with known
brightness properties; XMP sidecar output parses as valid XML with the expected
darktable tags; then the stage itself — XMP written for selected photos,
cross-camera reconciliation within a scene, per-photo failure isolation,
idempotency/force, and the selected-but-unclustered flag. The shared analysis lives
behind an injected :class:`~album.flow.photo_analyzer.PhotoAnalyzer`, here driven by a
fixed tone-estimator double so the reconciliation math is observable.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.analysis.tone import ToneParams
from album.db.models import Album, Photo
from album.db.session import SessionFactory, reuse
from album.flow.photo_analyzer import PhotoAnalyzer
from album.flow.stages.enhance import (
    _NAMESPACES,  # pyright: ignore[reportPrivateUsage]
    EnhanceResult,
    EnhanceStage,
    compute_camera_baseline,
    reconcile_tone,
    write_xmp_sidecar,
)
from tests.doubles import FakeThumbnails, FixedTone, RecordingCaptioner


def _solid_jpeg_bytes(value: int) -> bytes:
    """A small solid-color JPEG encoded via OpenCV, at a given brightness."""
    array = np.full((16, 16, 3), value, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", array)
    assert ok
    return buffer.tobytes()


_DARK = _solid_jpeg_bytes(40)
_BRIGHT = _solid_jpeg_bytes(200)

# A fixed non-zero delta so the cross-camera baseline reconciliation math is
# observable (independent of the fixture image).
_VALID_TONE = ToneParams(exposure_ev=0.2, temp_shift_k=100.0, shadows=0.1, highlights=-0.1)


class TestReconcileTone:
    """Pure baseline + AI-delta reconciliation (no DB, no images)."""

    def test_adds_baseline_and_ai_delta(self) -> None:
        baseline = ToneParams(exposure_ev=0.3, temp_shift_k=-50.0, shadows=0.0, highlights=0.0)
        ai_delta = ToneParams(exposure_ev=0.1, temp_shift_k=20.0, shadows=0.2, highlights=-0.1)

        combined = reconcile_tone(baseline, ai_delta)

        assert combined.exposure_ev == pytest.approx(0.4)
        assert combined.temp_shift_k == pytest.approx(-30.0)
        assert combined.shadows == pytest.approx(0.2)
        assert combined.highlights == pytest.approx(-0.1)

    def test_clamps_to_ai_result_bounds(self) -> None:
        # Even if a baseline is extreme, the combined result stays within the same
        # tone bounds the estimator holds to (AGENTS.md rule 5 — never drastic).
        baseline = ToneParams(exposure_ev=5.0, temp_shift_k=5000.0, shadows=5.0, highlights=-5.0)
        ai_delta = ToneParams()

        combined = reconcile_tone(baseline, ai_delta)

        assert -2.0 <= combined.exposure_ev <= 2.0
        assert -2000.0 <= combined.temp_shift_k <= 2000.0
        assert -1.0 <= combined.shadows <= 1.0
        assert -1.0 <= combined.highlights <= 1.0

    def test_with_zero_baseline_is_identity(self) -> None:
        ai_delta = ToneParams(exposure_ev=0.4, temp_shift_k=-200.0, shadows=0.3, highlights=-0.2)

        combined = reconcile_tone(ToneParams(), ai_delta)

        assert combined == ai_delta


class TestCameraBaseline:
    """Histogram-match baseline offset over small fixture images."""

    def test_is_zero_against_itself(self) -> None:
        baseline = compute_camera_baseline(_DARK, _DARK)

        assert baseline.exposure_ev == pytest.approx(0.0, abs=1e-6)

    def test_detects_brighter_camera(self) -> None:
        # camera brighter than reference -> a negative exposure correction
        # (pull it back down towards the reference's footing).
        baseline = compute_camera_baseline(_DARK, _BRIGHT)

        assert baseline.exposure_ev < 0.0

    def test_detects_darker_camera(self) -> None:
        baseline = compute_camera_baseline(_BRIGHT, _DARK)

        assert baseline.exposure_ev > 0.0


class TestXmpSidecar:
    """XMP sidecar output is darktable's real history stack (the only format
    ``darktable-cli`` actually applies — the previous flat-attribute schema was a
    silent no-op, caught by the step-22 render integration test)."""

    def _dt(self, name: str) -> str:
        return f"{{{_NAMESPACES['darktable']}}}{name}"

    def _entries(self, out_path: Path) -> dict[str, ET.Element]:
        root = ET.parse(out_path).getroot()  # raises if invalid XML
        seq = root.find(f".//{{{_NAMESPACES['darktable']}}}history/rdf:Seq", _NAMESPACES)
        assert seq is not None
        return {li.get(self._dt("operation")) or "": li for li in seq}

    def test_maps_all_knobs_to_darktable_module_entries(self, tmp_path: Path) -> None:
        tone = ToneParams(exposure_ev=0.5, temp_shift_k=150.0, shadows=0.2, highlights=-0.3)
        out_path = tmp_path / "IMG_0001.jpg.xmp"

        write_xmp_sidecar(out_path, tone)

        assert out_path.exists()
        entries = self._entries(out_path)
        # One module entry per non-zero knob (shadows+highlights share `shadhi`).
        assert set(entries) == {"exposure", "temperature", "shadhi"}
        exposure = entries["exposure"]
        assert exposure.get(self._dt("enabled")) == "1"
        assert exposure.get(self._dt("modversion")) == "6"
        assert exposure.get(self._dt("blendop_params"))  # every entry needs a blendop

        # The exposure EV round-trips through the real binary params blob.
        mode, black, ev, pct, target, bias = struct.unpack(
            "<iffffi",
            bytes.fromhex(exposure.get(self._dt("params")) or ""),
        )
        assert ev == pytest.approx(0.5)
        assert (mode, black, pct, target, bias) == (0, pytest.approx(0.0), 50.0, -4.0, 0)

    def test_shadhi_scales_knobs_to_darktable_range(self, tmp_path: Path) -> None:
        out_path = tmp_path / "IMG_0002.jpg.xmp"
        write_xmp_sidecar(out_path, ToneParams(shadows=0.2, highlights=-0.3))

        shadhi = self._entries(out_path)["shadhi"]
        fields = struct.unpack("<IffffffffIfI", bytes.fromhex(shadhi.get(self._dt("params")) or ""))
        # our [-1, 1] shadows/highlights land on darktable's [-100, 100].
        assert fields[2] == pytest.approx(20.0)  # shadows 0.2 -> 20
        assert fields[4] == pytest.approx(-30.0)  # highlights -0.3 -> -30

    def test_zero_knobs_produce_empty_history(self, tmp_path: Path) -> None:
        # An all-zero tone is a true no-op; no module entries are emitted.
        out_path = tmp_path / "IMG_0003.jpg.xmp"
        write_xmp_sidecar(out_path, ToneParams())

        assert ET.parse(out_path)  # still valid XML
        assert self._entries(out_path) == {}


class TestEnhanceStage:
    """`EnhanceStage.run`: XMP output, cross-camera reconciliation, isolation."""

    def _persist(self, session: Session, album: Album) -> int:
        session.add(album)
        session.commit()
        assert album.id is not None
        return album.id

    def _stage(
        self,
        thumbnails: FakeThumbnails,
        tmp_path: Path,
        album_id: int,
        session_factory: SessionFactory,
        *,
        force: bool = False,
    ) -> EnhanceStage:
        analyzer = PhotoAnalyzer(
            thumbnails,
            tone_estimator=FixedTone(_VALID_TONE),
            captioner=RecordingCaptioner("A test photo."),
        )
        return EnhanceStage(
            analyzer,
            thumbnails,
            album_id,
            session_factory,
            data_root=tmp_path,
            force=force,
        )

    def test_writes_xmp_and_sets_flag_for_selected_photos(
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
                scene_id=1,
                camera_model="CamA",
            ),
        ]
        album_id = self._persist(session, album)

        result = self._stage(
            FakeThumbnails({"a1": _DARK}),
            tmp_path,
            album_id,
            reuse(session),
        ).run()

        assert isinstance(result, EnhanceResult)
        assert result.enhanced == 1
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.xmp_written is True
        xmp_path = tmp_path / "test-album" / "xmp" / "a1.jpg.xmp"
        assert xmp_path.exists()
        ET.parse(xmp_path)  # valid XML

    def test_skips_unselected_photos(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=False, scene_id=1),
        ]
        album_id = self._persist(session, album)

        result = self._stage(FakeThumbnails(), tmp_path, album_id, reuse(session)).run()

        assert result.enhanced == 0
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.xmp_written is False

    def test_is_idempotent_and_force_reprocesses(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True, scene_id=1),
        ]
        album_id = self._persist(session, album)
        thumbs = FakeThumbnails({"a1": _DARK})

        first = self._stage(thumbs, tmp_path, album_id, reuse(session)).run()
        second = self._stage(thumbs, tmp_path, album_id, reuse(session)).run()
        forced = self._stage(thumbs, tmp_path, album_id, reuse(session), force=True).run()

        assert first.enhanced == 1
        assert second.enhanced == 0
        assert second.skipped == 1
        assert forced.enhanced == 1

    def test_reconciles_cross_camera_baseline_within_scene(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # Two cameras in the same scene: CamA is the reference (first by timestamp),
        # CamB is much brighter -> gets a baseline correction pulling it down, on
        # top of its own AI delta.
        album = make_album(slug="test-album")
        ref = make_photo(
            album,
            immich_asset_id="ref",
            filename="ref.jpg",
            selected=True,
            scene_id=1,
            camera_model="CamA",
            timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
        other = make_photo(
            album,
            immich_asset_id="other",
            filename="other.jpg",
            selected=True,
            scene_id=1,
            camera_model="CamB",
            timestamp=datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
        )
        album.photos = [ref, other]
        album_id = self._persist(session, album)

        self._stage(
            FakeThumbnails({"ref": _DARK, "other": _BRIGHT}),
            tmp_path,
            album_id,
            reuse(session),
        ).run()

        photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
        # The brighter camera's final exposure correction should be pulled down
        # relative to the AI's raw per-photo delta (0.2) by the baseline offset.
        assert photos["other"].enhance_exposure_ev < photos["ref"].enhance_exposure_ev  # pyright: ignore[reportOperatorIssue]

    def test_isolates_per_photo_failure(
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
                immich_asset_id="good",
                filename="good.jpg",
                selected=True,
                scene_id=1,
                camera_model="CamA",
            ),
            # Different camera than the reference, so its thumbnail is fetched for
            # histogram-match baseline computation -> exercises the decode failure.
            make_photo(
                album,
                immich_asset_id="bad",
                filename="bad.jpg",
                selected=True,
                scene_id=1,
                camera_model="CamB",
            ),
        ]
        album_id = self._persist(session, album)
        thumbs = FakeThumbnails({"good": _DARK, "bad": b"not-an-image"})

        result = self._stage(thumbs, tmp_path, album_id, reuse(session)).run()

        assert result.enhanced == 1
        assert result.failed == 1
        photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
        assert photos["good"].xmp_written is True
        assert photos["bad"].xmp_written is False

    def test_scoped_to_album(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        other_album = make_album(slug="other")
        other_album.photos = [
            make_photo(
                other_album,
                immich_asset_id="o1",
                filename="o1.jpg",
                selected=True,
                scene_id=1,
            ),
        ]
        other_id = self._persist(session, other_album)
        target_album = make_album(slug="target")
        target_album.photos = [
            make_photo(
                target_album,
                immich_asset_id="t1",
                filename="t1.jpg",
                selected=True,
                scene_id=1,
            ),
        ]
        target_id = self._persist(session, target_album)

        self._stage(FakeThumbnails(), tmp_path, target_id, reuse(session)).run()

        other_photo = session.scalars(sa_select(Photo).where(Photo.album_id == other_id)).one()
        assert other_photo.xmp_written is False
        target_photo = session.scalars(sa_select(Photo).where(Photo.album_id == target_id)).one()
        assert target_photo.xmp_written is True

    def test_raises_for_unknown_album(self, session: Session, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            self._stage(FakeThumbnails(), tmp_path, 999, reuse(session)).run()

    def test_flags_selected_but_unclustered_photo_instead_of_dropping_it(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # cluster.py explicitly allows a selected photo to have no scene_id (its
        # own out_of_range/undated cases) — enhance must not silently skip it.
        album = make_album(slug="test-album")
        album.photos = [
            make_photo(
                album,
                immich_asset_id="a1",
                filename="a1.jpg",
                selected=True,
                scene_id=None,
            ),
        ]
        album_id = self._persist(session, album)

        result = self._stage(FakeThumbnails(), tmp_path, album_id, reuse(session)).run()

        assert result.unclustered == 1
        assert result.enhanced == 0
        assert result.failed == 0
        photo = session.scalars(sa_select(Photo)).one()
        assert photo.xmp_written is False

    def test_force_does_not_compound_baseline_on_repeated_runs(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # Regression test: a forced re-run must not re-apply the camera baseline
        # on top of an already-reconciled value.
        album = make_album(slug="test-album")
        ref = make_photo(
            album,
            immich_asset_id="ref",
            filename="ref.jpg",
            selected=True,
            scene_id=1,
            camera_model="CamA",
            timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
        other = make_photo(
            album,
            immich_asset_id="other",
            filename="other.jpg",
            selected=True,
            scene_id=1,
            camera_model="CamB",
            timestamp=datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
        )
        album.photos = [ref, other]
        album_id = self._persist(session, album)
        thumbs = FakeThumbnails({"ref": _DARK, "other": _BRIGHT})

        self._stage(thumbs, tmp_path, album_id, reuse(session)).run()
        other_photo = session.scalars(
            sa_select(Photo).where(Photo.immich_asset_id == "other"),
        ).one()
        first_run_exposure = other_photo.enhance_exposure_ev

        # Force a second run — same AI model/inputs, should reproduce the same
        # reconciled result rather than drifting further from it.
        self._stage(thumbs, tmp_path, album_id, reuse(session), force=True).run()
        other_photo = session.scalars(
            sa_select(Photo).where(Photo.immich_asset_id == "other"),
        ).one()
        second_run_exposure = other_photo.enhance_exposure_ev

        assert second_run_exposure == pytest.approx(first_run_exposure)  # pyright: ignore[reportArgumentType]

    def test_still_corrects_two_unlabeled_cameras_in_same_scene(
        self,
        session: Session,
        tmp_path: Path,
        make_album: Callable[..., Album],
        make_photo: Callable[..., Photo],
    ) -> None:
        # Two photos with camera_model=None must NOT be treated as "the same
        # camera" (that would silently skip baseline correction for exactly the
        # case — missing EXIF — where cross-camera drift is most likely).
        album = make_album(slug="test-album")
        ref = make_photo(
            album,
            immich_asset_id="ref",
            filename="ref.jpg",
            selected=True,
            scene_id=1,
            camera_model=None,
            timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
        other = make_photo(
            album,
            immich_asset_id="other",
            filename="other.jpg",
            selected=True,
            scene_id=1,
            camera_model=None,
            timestamp=datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
        )
        album.photos = [ref, other]
        album_id = self._persist(session, album)

        self._stage(
            FakeThumbnails({"ref": _DARK, "other": _BRIGHT}),
            tmp_path,
            album_id,
            reuse(session),
        ).run()

        photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
        assert photos["other"].enhance_exposure_ev < photos["ref"].enhance_exposure_ev  # pyright: ignore[reportOperatorIssue]
