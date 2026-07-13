"""Tests for the ``enhance`` stage (build step 10).

Per enhance.md / architecture.md's "one combined vision call": ``ensure_analysis``
is the shared idempotent trigger point that both ``enhance`` and the (separately
built) ``caption`` stage rely on, so its no-double-call contract is tested
explicitly with PydanticAI's ``TestModel``/``FunctionModel`` (build-order step 10).

Covers, in order: the pure baseline+AI-delta reconciliation math (no DB, no
images); histogram-match baseline offset over small fixture images with known
brightness properties; XMP sidecar output parses as valid XML with the expected
darktable tags; the shared analysis call is not repeated for an
already-populated photo; a segment-less/no-context photo still enhances without
erroring; per-photo failure isolation; idempotency/force on the stage as a whole.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo
from album.stages.enhance import (
    _NAMESPACES,  # pyright: ignore[reportPrivateUsage]
    EnhanceResult,
    ToneParams,
    compute_camera_baseline,
    enhance,
    ensure_analysis,
    reconcile_tone,
    write_xmp_sidecar,
)

from .conftest import make_album, make_photo

_IMAGES = Path(__file__).parent / "fixtures" / "images"


def _solid_jpeg_bytes(value: int) -> bytes:
    """A small solid-color JPEG encoded via OpenCV, at a given brightness."""
    import cv2

    array = np.full((16, 16, 3), value, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", array)
    assert ok
    return buffer.tobytes()


_DARK = _solid_jpeg_bytes(40)
_BRIGHT = _solid_jpeg_bytes(200)


class FakeThumbnails:
    def __init__(self, by_asset: dict[str, bytes], default: bytes = _DARK) -> None:
        self._by_asset = by_asset
        self._default = default
        self.calls: list[str] = []

    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes:
        self.calls.append(asset_id)
        return self._by_asset.get(asset_id, self._default)


def _valid_result_model() -> TestModel:
    return TestModel(
        custom_output_args={
            "exposure_ev": 0.2,
            "temp_shift_k": 100.0,
            "shadows": 0.1,
            "highlights": -0.1,
            "caption": "A test photo.",
        }
    )


def _persist(session: Session, album: Album) -> int:
    session.add(album)
    session.commit()
    assert album.id is not None
    return album.id


# --- Pure reconciliation math ---------------------------------------------


def test_reconcile_tone_adds_baseline_and_ai_delta() -> None:
    baseline = ToneParams(exposure_ev=0.3, temp_shift_k=-50.0, shadows=0.0, highlights=0.0)
    ai_delta = ToneParams(exposure_ev=0.1, temp_shift_k=20.0, shadows=0.2, highlights=-0.1)

    combined = reconcile_tone(baseline, ai_delta)

    assert combined.exposure_ev == pytest.approx(0.4)
    assert combined.temp_shift_k == pytest.approx(-30.0)
    assert combined.shadows == pytest.approx(0.2)
    assert combined.highlights == pytest.approx(-0.1)


def test_reconcile_tone_clamps_to_ai_result_bounds() -> None:
    # Even if a baseline is extreme, the combined result stays within the same
    # bounds AnalysisResult enforces (AGENTS.md rule 5 — never drastic).
    baseline = ToneParams(exposure_ev=5.0, temp_shift_k=5000.0, shadows=5.0, highlights=-5.0)
    ai_delta = ToneParams(exposure_ev=0.0, temp_shift_k=0.0, shadows=0.0, highlights=0.0)

    combined = reconcile_tone(baseline, ai_delta)

    assert -2.0 <= combined.exposure_ev <= 2.0
    assert -2000.0 <= combined.temp_shift_k <= 2000.0
    assert -1.0 <= combined.shadows <= 1.0
    assert -1.0 <= combined.highlights <= 1.0


def test_reconcile_tone_with_zero_baseline_is_identity() -> None:
    ai_delta = ToneParams(exposure_ev=0.4, temp_shift_k=-200.0, shadows=0.3, highlights=-0.2)

    combined = reconcile_tone(ToneParams(), ai_delta)

    assert combined == ai_delta


# --- Histogram-match baseline over fixture images --------------------------


def test_compute_camera_baseline_is_zero_against_itself() -> None:
    baseline = compute_camera_baseline(_DARK, _DARK)

    assert baseline.exposure_ev == pytest.approx(0.0, abs=1e-6)


def test_compute_camera_baseline_detects_brighter_camera() -> None:
    # camera_image brighter than reference -> a negative exposure correction
    # baseline (pull it back down towards the reference's footing).
    baseline = compute_camera_baseline(_DARK, _BRIGHT)

    assert baseline.exposure_ev < 0.0


def test_compute_camera_baseline_detects_darker_camera() -> None:
    baseline = compute_camera_baseline(_BRIGHT, _DARK)

    assert baseline.exposure_ev > 0.0


# --- XMP sidecar output ------------------------------------------------------


def test_write_xmp_sidecar_produces_valid_xml_with_expected_tags(tmp_path: Path) -> None:
    tone = ToneParams(exposure_ev=0.5, temp_shift_k=150.0, shadows=0.2, highlights=-0.3)
    out_path = tmp_path / "IMG_0001.jpg.xmp"

    write_xmp_sidecar(out_path, tone)

    assert out_path.exists()
    tree = ET.parse(out_path)  # raises if invalid XML
    root = tree.getroot()
    description = root.find(".//rdf:Description", _NAMESPACES)
    assert description is not None
    assert description.get(f"{{{_NAMESPACES['darktable']}}}exposure") == "0.5"
    assert description.get(f"{{{_NAMESPACES['darktable']}}}temperature_offset") == "150.0"
    assert description.get(f"{{{_NAMESPACES['darktable']}}}shadows") == "0.2"
    assert description.get(f"{{{_NAMESPACES['darktable']}}}highlights") == "-0.3"


# --- ensure_analysis idempotency (shared trigger point) --------------------


def test_ensure_analysis_calls_agent_and_persists_all_fields(session: Session) -> None:
    album = make_album()
    album.photos = [make_photo(album, immich_asset_id="a1")]
    _persist(session, album)
    photo = session.scalars(sa_select(Photo)).one()

    result = ensure_analysis(
        session, photo, thumbnails=FakeThumbnails({}), model=_valid_result_model()
    )

    assert result is not None
    assert photo.enhance_exposure_ev == result.exposure_ev
    assert photo.enhance_temp_shift_k == result.temp_shift_k
    assert photo.enhance_shadows == result.shadows
    assert photo.enhance_highlights == result.highlights
    assert photo.caption == result.caption


def test_ensure_analysis_is_not_repeated_for_an_already_populated_photo(session: Session) -> None:
    album = make_album()
    album.photos = [make_photo(album, immich_asset_id="a1")]
    _persist(session, album)
    photo = session.scalars(sa_select(Photo)).one()

    call_count = 0

    def counting_model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:  # pragma: no cover - exercised via FunctionModel
        nonlocal call_count
        call_count += 1
        return ModelResponse(
            parts=[
                TextPart(
                    content=(
                        '{"exposure_ev": 0.0, "temp_shift_k": 0.0, "shadows": 0.0, '
                        '"highlights": 0.0, "caption": "x"}'
                    )
                )
            ]
        )

    model = FunctionModel(counting_model)
    thumbnails = FakeThumbnails({})

    first = ensure_analysis(session, photo, thumbnails=thumbnails, model=model)
    second = ensure_analysis(session, photo, thumbnails=thumbnails, model=model)

    assert first is not None
    assert second is None  # no-op: already analyzed
    assert call_count == 1
    assert thumbnails.calls == ["a1"]  # thumbnail fetched only once


def test_ensure_analysis_handles_no_segment_context_without_erroring(session: Session) -> None:
    album = make_album()  # no segments
    album.photos = [make_photo(album, immich_asset_id="a1", segment=None)]
    _persist(session, album)
    photo = session.scalars(sa_select(Photo)).one()

    result = ensure_analysis(
        session, photo, thumbnails=FakeThumbnails({}), model=_valid_result_model()
    )

    assert result is not None
    assert photo.caption


# --- enhance() stage orchestration ------------------------------------------


def test_enhance_writes_xmp_and_sets_flag_for_selected_photos(
    session: Session, tmp_path: Path
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
    album_id = _persist(session, album)

    result = enhance(
        album_id,
        session,
        FakeThumbnails({"a1": _DARK}),
        _valid_result_model(),
        data_root=tmp_path,
    )

    assert isinstance(result, EnhanceResult)
    assert result.enhanced == 1
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.xmp_written is True
    xmp_path = tmp_path / "test-album" / "xmp" / "a1.jpg.xmp"
    assert xmp_path.exists()
    ET.parse(xmp_path)  # valid XML


def test_enhance_skips_unselected_photos(session: Session, tmp_path: Path) -> None:
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=False, scene_id=1),
    ]
    album_id = _persist(session, album)

    result = enhance(
        album_id, session, FakeThumbnails({}), _valid_result_model(), data_root=tmp_path
    )

    assert result.enhanced == 0
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.xmp_written is False


def test_enhance_is_idempotent_and_force_reprocesses(session: Session, tmp_path: Path) -> None:
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(
            album,
            immich_asset_id="a1",
            filename="a1.jpg",
            selected=True,
            scene_id=1,
        ),
    ]
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({"a1": _DARK})

    first = enhance(album_id, session, thumbs, _valid_result_model(), data_root=tmp_path)
    second = enhance(album_id, session, thumbs, _valid_result_model(), data_root=tmp_path)
    forced = enhance(
        album_id, session, thumbs, _valid_result_model(), data_root=tmp_path, force=True
    )

    assert first.enhanced == 1
    assert second.enhanced == 0
    assert second.skipped == 1
    assert forced.enhanced == 1


def test_enhance_reconciles_cross_camera_baseline_within_scene(
    session: Session, tmp_path: Path
) -> None:
    # Two cameras in the same scene: CamA is the reference (first by timestamp),
    # CamB is much brighter -> gets a baseline correction pulling it down, on
    # top of its own AI delta.
    from datetime import UTC, datetime

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
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({"ref": _DARK, "other": _BRIGHT})

    enhance(album_id, session, thumbs, _valid_result_model(), data_root=tmp_path)

    photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
    # The brighter camera's final exposure correction should be pulled down
    # relative to the AI's raw per-photo delta (0.2) by the baseline offset.
    assert photos["other"].enhance_exposure_ev < photos["ref"].enhance_exposure_ev  # pyright: ignore[reportOperatorIssue]


def test_enhance_isolates_per_photo_failure(session: Session, tmp_path: Path) -> None:
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
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({"good": _DARK, "bad": b"not-an-image"})

    result = enhance(album_id, session, thumbs, _valid_result_model(), data_root=tmp_path)

    assert result.enhanced == 1
    assert result.failed == 1
    photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
    assert photos["good"].xmp_written is True
    assert photos["bad"].xmp_written is False


def test_enhance_scoped_to_album(session: Session, tmp_path: Path) -> None:
    other_album = make_album(slug="other")
    other_album.photos = [
        make_photo(other_album, immich_asset_id="o1", filename="o1.jpg", selected=True, scene_id=1)
    ]
    other_id = _persist(session, other_album)
    target_album = make_album(slug="target")
    target_album.photos = [
        make_photo(target_album, immich_asset_id="t1", filename="t1.jpg", selected=True, scene_id=1)
    ]
    target_id = _persist(session, target_album)

    enhance(target_id, session, FakeThumbnails({}), _valid_result_model(), data_root=tmp_path)

    other_photo = session.scalars(sa_select(Photo).where(Photo.album_id == other_id)).one()
    assert other_photo.xmp_written is False
    target_photo = session.scalars(sa_select(Photo).where(Photo.album_id == target_id)).one()
    assert target_photo.xmp_written is True


def test_enhance_raises_for_unknown_album(session: Session, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        enhance(999, session, FakeThumbnails({}), _valid_result_model(), data_root=tmp_path)


def test_enhance_flags_selected_but_unclustered_photo_instead_of_dropping_it(
    session: Session, tmp_path: Path
) -> None:
    # cluster.py explicitly allows a selected photo to have no scene_id (its
    # own out_of_range/undated cases) — enhance must not silently skip it.
    album = make_album(slug="test-album")
    album.photos = [
        make_photo(album, immich_asset_id="a1", filename="a1.jpg", selected=True, scene_id=None),
    ]
    album_id = _persist(session, album)

    result = enhance(
        album_id, session, FakeThumbnails({}), _valid_result_model(), data_root=tmp_path
    )

    assert result.unclustered == 1
    assert result.enhanced == 0
    assert result.failed == 0
    photo = session.scalars(sa_select(Photo)).one()
    assert photo.xmp_written is False


def test_enhance_force_does_not_compound_baseline_on_repeated_runs(
    session: Session, tmp_path: Path
) -> None:
    # Regression test: a forced re-run must not re-apply the camera baseline
    # on top of an already-reconciled value. ensure_analysis persists the
    # *reconciled* total into enhance_exposure_ev after the first run, so a
    # naive force re-run that treats that stored value as a fresh "AI delta"
    # would keep adding the baseline offset every time it's forced.
    from datetime import UTC, datetime

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
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({"ref": _DARK, "other": _BRIGHT})

    enhance(album_id, session, thumbs, _valid_result_model(), data_root=tmp_path)
    other_photo = session.scalars(sa_select(Photo).where(Photo.immich_asset_id == "other")).one()
    first_run_exposure = other_photo.enhance_exposure_ev

    # Force a second run — same AI model/inputs, should reproduce the same
    # reconciled result rather than drifting further from it.
    enhance(album_id, session, thumbs, _valid_result_model(), data_root=tmp_path, force=True)
    other_photo = session.scalars(sa_select(Photo).where(Photo.immich_asset_id == "other")).one()
    second_run_exposure = other_photo.enhance_exposure_ev

    assert second_run_exposure == pytest.approx(first_run_exposure)  # pyright: ignore[reportArgumentType]


def test_enhance_still_corrects_two_unlabeled_cameras_in_same_scene(
    session: Session, tmp_path: Path
) -> None:
    # Two photos with camera_model=None must NOT be treated as "the same
    # camera" (that would silently skip baseline correction for exactly the
    # case — missing EXIF — where cross-camera drift is most likely).
    from datetime import UTC, datetime

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
    album_id = _persist(session, album)
    thumbs = FakeThumbnails({"ref": _DARK, "other": _BRIGHT})

    enhance(album_id, session, thumbs, _valid_result_model(), data_root=tmp_path)

    photos = {p.immich_asset_id: p for p in session.scalars(sa_select(Photo))}
    assert photos["other"].enhance_exposure_ev < photos["ref"].enhance_exposure_ev  # pyright: ignore[reportOperatorIssue]
