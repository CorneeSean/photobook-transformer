"""``enhance`` — AI estimates bounded tone parameters; classical code applies
them via XMP sidecars, scoped by ``album_id``.

Two responsibilities, per [enhance.md](/stages/enhance.md):

1. **Trigger the shared vision call** (:func:`ensure_analysis`) — the "one
   combined vision call" design in [architecture.md](/architecture.md): a
   single agent call per selected photo produces bounded tone deltas *and* a
   caption together, because uploading the thumbnail is the expensive part.
   This module and the (separately built) ``caption`` stage both need that
   call to have run, exactly once, before they read their half of the result.
   :func:`ensure_analysis` is the shared idempotent trigger point both stages
   call — see its docstring for the exact no-op contract a `caption`
   implementation must match or reuse.
2. **Reconcile cross-camera color consistency** (:func:`compute_camera_baseline`
   + :func:`reconcile_tone`) and write the final per-photo parameters as **XMP
   sidecar files** that [`render`](./render.py) (darktable-cli) will read.

XMP schema choice
------------------
We write **darktable's own history-stack attributes** in the sidecar (the
``darktable:`` namespace), not Adobe's Camera Raw (``crs:``) schema, because
``render`` invokes ``darktable-cli`` directly (tech-stack.md) — darktable reads
its own sidecar format natively with no import/conversion step. We keep the
history minimal and flat (one ``rdf:Description`` carrying
``darktable:exposure``, ``darktable:temperature_offset``, ``darktable:shadows``,
``darktable:highlights`` as plain float attributes) rather than a full
multi-``iop`` history stack — enough for `render` (step 11) to read the four
numbers back and apply them through darktable's real exposure/temperature/
shadows-and-highlights modules; it can promote this to a richer history stack
later without changing what `enhance` writes.

Sidecar path: ``<data_root>/<album.slug>/xmp/<photo.filename>.xmp`` (e.g.
``data/albums/honeymoon-2026/xmp/IMG_0001.jpg.xmp``), mirroring
``data/albums/<slug>/rendered/`` that `render` already writes to
(folder-structure.md). ``data_root`` defaults to ``data/albums`` but is an
injectable parameter so tests never touch the real filesystem.

No Prefect imports (folder-structure.md rule). ``album_id`` is always the
first argument. Idempotent: skips photos whose ``xmp_written`` is already
``True`` unless ``force=True``; per-photo failures are isolated (try/except +
log + continue), consistent with ``quality.py``.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from pydantic_ai.models import Model
from skimage.exposure import match_histograms
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.agents.analysis import AnalysisResult, PhotoContext, analyze_photo
from album.db.models import Album, Photo

_log = logging.getLogger(__name__)

_DEFAULT_DATA_ROOT = Path("data/albums")

# darktable's own XMP namespace convention: reverse-DNS style URI, "darktable"
# prefix. Made up here (this project isn't literally registered with them) but
# structurally matches how darktable itself emits sidecars.
_DARKTABLE_NS = "http://darktable.sf.net/xmp/1.0/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_X_NS = "adobe:ns:meta/"

_NAMESPACES = {"darktable": _DARKTABLE_NS, "rdf": _RDF_NS, "x": _X_NS}


class ThumbnailSource(Protocol):
    def get_thumbnail(self, asset_id: str, *, size: str = "thumbnail") -> bytes: ...


@dataclass(frozen=True)
class ToneParams:
    """The same four bounded tone knobs as :class:`AnalysisResult`, but
    freestanding — used for the baseline offset, the AI delta, and their
    reconciled sum alike, so :func:`reconcile_tone` can be pure arithmetic
    over one type instead of juggling three."""

    exposure_ev: float = 0.0
    temp_shift_k: float = 0.0
    shadows: float = 0.0
    highlights: float = 0.0


@dataclass
class EnhanceResult:
    enhanced: int = 0
    skipped: int = 0
    failed: int = 0
    # selected photos with no scene_id (cluster.py's out_of_range/undated
    # photos — see cluster.md) — flagged rather than silently dropped, mirroring
    # cluster.py's own ClusterResult.out_of_range/undated pattern.
    unclustered: int = 0


def ensure_analysis(
    session: Session,
    photo: Photo,
    *,
    thumbnails: ThumbnailSource,
    model: Model | None = None,
    force: bool = False,
) -> AnalysisResult | None:
    """Shared idempotent trigger for the one-combined-vision-call design
    (architecture.md's "One combined vision call, not two").

    This is the **single place** that should ever call
    :func:`album.agents.analysis.analyze_photo` for a given photo. Both this
    module's :func:`enhance` and the ``caption`` stage (built independently,
    step 12) must route through this function — or reimplement this exact
    no-op check — so a photo is never analyzed twice.

    Contract:
    - If ``photo`` already has all five stored fields populated
      (``enhance_exposure_ev``, ``enhance_temp_shift_k``, ``enhance_shadows``,
      ``enhance_highlights``, ``caption``) and ``force`` is ``False``, this is
      a no-op: returns ``None`` and does **not** touch the network or the row.
    - Otherwise: fetches a thumbnail via ``thumbnails``, builds a
      ``PhotoContext`` from the photo's segment (label/place, both optional —
      an unsegmented album or an out-of-range photo simply gets a context with
      neither set, never an error) and ``Album.meta``, calls ``analyze_photo``,
      writes all five fields onto ``photo`` in one shot, and returns the fresh
      :class:`AnalysisResult`.

    ``force`` matters because :func:`enhance` overwrites ``enhance_exposure_ev``
    et al. with the *final, reconciled* (baseline + AI delta) values, not the
    raw AI delta — so once a photo has been enhanced once, those columns no
    longer hold a value ``reconcile_tone`` can be fed a second time without
    double-applying the baseline. A ``force=True`` re-run therefore must
    re-fetch the raw delta from a fresh analysis call rather than reading the
    (now-reconciled) stored columns — this flag is what makes that possible.

    Does not ``flush``/``commit`` — the caller (e.g. :func:`enhance`'s per-photo
    loop) controls the transaction boundary, matching ``quality.py``'s style.
    """
    already_analyzed = (
        photo.enhance_exposure_ev is not None
        and photo.enhance_temp_shift_k is not None
        and photo.enhance_shadows is not None
        and photo.enhance_highlights is not None
        and photo.caption is not None
    )
    if already_analyzed and not force:
        return None

    image = thumbnails.get_thumbnail(photo.immich_asset_id)
    context = PhotoContext(
        segment_label=photo.segment.label if photo.segment is not None else None,
        segment_place=photo.segment.place if photo.segment is not None else None,
        album_meta=photo.album.meta,
    )
    result = analyze_photo(image, context, model=model)

    photo.enhance_exposure_ev = result.exposure_ev
    photo.enhance_temp_shift_k = result.temp_shift_k
    photo.enhance_shadows = result.shadows
    photo.enhance_highlights = result.highlights
    photo.caption = result.caption
    return result


def reconcile_tone(baseline: ToneParams, ai_delta: ToneParams) -> ToneParams:
    """Combine a per-camera baseline correction with the AI's per-photo delta.

    Per enhance.md: "Different cameras land on a common footing first;
    individual exposure mistakes get fixed second" — baseline first, AI delta
    on top, i.e. a plain sum per channel. The result is clamped to the same
    bounds :class:`AnalysisResult` enforces on the AI's own output (AGENTS.md
    rule 5 — AI/derived corrections are bounded, never drastic), even though
    the baseline itself is computed independently of that model.

    Pure function, no DB/image I/O — directly unit-testable.
    """
    return ToneParams(
        exposure_ev=_clamp(baseline.exposure_ev + ai_delta.exposure_ev, -2.0, 2.0),
        temp_shift_k=_clamp(baseline.temp_shift_k + ai_delta.temp_shift_k, -2000.0, 2000.0),
        shadows=_clamp(baseline.shadows + ai_delta.shadows, -1.0, 1.0),
        highlights=_clamp(baseline.highlights + ai_delta.highlights, -1.0, 1.0),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_camera_baseline(reference_image: bytes, camera_image: bytes) -> ToneParams:
    """Histogram-match ``camera_image`` against ``reference_image`` and derive
    a baseline *exposure* offset from the shift in mean luminance.

    Uses ``skimage.exposure.match_histograms`` (enhance.md) to find the tonal
    mapping that would put ``camera_image`` on the reference's footing, then
    expresses the *brightness* component of that mapping as an EV offset
    (log2 of the ratio of post-match to pre-match mean luma) — the piece
    `render`'s darktable exposure module can actually apply. White-balance/
    shadows/highlights baselines are left at 0.0 here; only exposure has a
    robust, simple scalar summary of a full histogram match. A future
    refinement could derive per-channel color-temperature and shadow/highlight
    baselines the same way, but exposure is the dominant cross-camera drift in
    practice and is what enhance.md's example targets.
    """
    reference = _decode_gray(reference_image)
    camera = _decode_gray(camera_image)
    matched = match_histograms(camera, reference)

    camera_mean = float(np.mean(camera))
    matched_mean = float(np.mean(matched))
    if camera_mean <= 0.0:
        exposure_ev = 0.0
    else:
        exposure_ev = float(np.log2(max(matched_mean, 1e-6) / camera_mean))

    return ToneParams(exposure_ev=_clamp(exposure_ev, -2.0, 2.0))


def _decode_gray(image: bytes) -> np.ndarray:
    buffer = np.frombuffer(image, dtype=np.uint8)
    decoded = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise ValueError("could not decode image bytes")
    return decoded.astype(np.float64)


def write_xmp_sidecar(path: Path, tone: ToneParams) -> None:
    """Write ``tone`` as a darktable-style XMP sidecar at ``path``.

    See the module docstring for the schema choice (darktable's own
    ``darktable:`` namespace, flat attributes rather than a full ``iop``
    history stack). Creates parent directories as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    ET.register_namespace("x", _X_NS)
    ET.register_namespace("rdf", _RDF_NS)
    ET.register_namespace("darktable", _DARKTABLE_NS)

    xmpmeta = ET.Element(f"{{{_X_NS}}}xmpmeta")
    rdf = ET.SubElement(xmpmeta, f"{{{_RDF_NS}}}RDF")
    description = ET.SubElement(rdf, f"{{{_RDF_NS}}}Description")
    description.set(f"{{{_RDF_NS}}}about", "")
    description.set(f"{{{_DARKTABLE_NS}}}exposure", str(tone.exposure_ev))
    description.set(f"{{{_DARKTABLE_NS}}}temperature_offset", str(tone.temp_shift_k))
    description.set(f"{{{_DARKTABLE_NS}}}shadows", str(tone.shadows))
    description.set(f"{{{_DARKTABLE_NS}}}highlights", str(tone.highlights))

    ET.ElementTree(xmpmeta).write(path, encoding="UTF-8", xml_declaration=True)


def _xmp_path(data_root: Path, album_slug: str, filename: str) -> Path:
    return data_root / album_slug / "xmp" / f"{filename}.xmp"


def enhance(
    album_id: int,
    session: Session,
    thumbnails: ThumbnailSource,
    model: Model | None,
    *,
    data_root: Path = _DEFAULT_DATA_ROOT,
    force: bool = False,
) -> EnhanceResult:
    """Enhance every ``selected`` photo in ``album_id``: ensure the shared
    analysis has run, reconcile cross-camera baseline with the AI's per-photo
    delta, and write an XMP sidecar. Idempotent unless ``force``.

    Reference-photo-per-scene heuristic: within each ``scene_id`` group, the
    photo with the **earliest timestamp** (ties broken by ``id`` for
    determinism) is the reference every other camera in that scene is matched
    against. Timestamp (not aesthetic score) is used because it's always
    comparable and doesn't depend on `quality` having run against every photo
    in the scene the same way; the *first* photo of a scene is also a
    reasonable stand-in for "how this moment actually looked" before any
    per-camera drift.
    """
    album = session.get(Album, album_id)
    if album is None:
        raise ValueError(f"no album with id {album_id}")

    photos = list(
        session.scalars(
            select(Photo)
            .where(Photo.album_id == album_id, Photo.selected == True)  # noqa: E712
            .order_by(Photo.id)
        )
    )

    by_scene: dict[int, list[Photo]] = {}
    result = EnhanceResult()
    for photo in photos:
        if photo.scene_id is not None:
            by_scene.setdefault(photo.scene_id, []).append(photo)
        else:
            # Selected but never clustered (cluster.py's out_of_range/undated
            # photos can still be `selected`) — flagged, not silently dropped;
            # there's no scene to reconcile a cross-camera baseline against.
            _log.warning(
                "photo %s (%s) is selected but has no scene_id — skipping enhance",
                photo.id,
                photo.filename,
            )
            result.unclustered += 1

    for scene_id in sorted(by_scene):
        _enhance_scene(
            by_scene[scene_id],
            session=session,
            thumbnails=thumbnails,
            model=model,
            data_root=data_root,
            album_slug=album.slug,
            force=force,
            result=result,
        )

    session.flush()
    return result


def _enhance_scene(
    scene_photos: list[Photo],
    *,
    session: Session,
    thumbnails: ThumbnailSource,
    model: Model | None,
    data_root: Path,
    album_slug: str,
    force: bool,
    result: EnhanceResult,
) -> None:
    reference = min(
        scene_photos,
        key=lambda p: (p.timestamp is None, p.timestamp, p.id),
    )
    reference_image_cache: bytes | None = None

    for photo in scene_photos:
        already_done = photo.xmp_written and not force
        if already_done:
            result.skipped += 1
            continue

        try:
            # force=True here (not just at the xmp_written skip above): once a
            # photo has been enhanced, enhance_* columns hold the *reconciled*
            # (baseline + delta) value, not the raw AI delta — so a forced
            # re-run must re-derive the raw delta from a fresh analysis call
            # rather than reading those now-reconciled columns as if they were
            # still the AI's untouched output (see ensure_analysis's docstring).
            analysis = ensure_analysis(
                session, photo, thumbnails=thumbnails, model=model, force=force
            )
            if analysis is not None:
                ai_delta = ToneParams(
                    exposure_ev=analysis.exposure_ev,
                    temp_shift_k=analysis.temp_shift_k,
                    shadows=analysis.shadows,
                    highlights=analysis.highlights,
                )
            else:
                # Only reachable when force=False and the photo was already
                # analyzed but never reconciled/xmp_written (e.g. a previous
                # run crashed between ensure_analysis and the XMP write) — the
                # stored columns still hold the untouched raw AI delta. Only
                # the four tone numbers are needed here (not a full
                # AnalysisResult, whose ``caption`` field enforces
                # ``min_length=1`` and has no bearing on this stage).
                ai_delta = ToneParams(
                    exposure_ev=photo.enhance_exposure_ev or 0.0,
                    temp_shift_k=photo.enhance_temp_shift_k or 0.0,
                    shadows=photo.enhance_shadows or 0.0,
                    highlights=photo.enhance_highlights or 0.0,
                )

            # Only treat a photo as "same camera as reference, no baseline
            # needed" when camera_model is a known, matching, non-null value.
            # Two photos both missing EXIF camera_model (common for
            # screenshots or re-compressed shares) are *not* assumed to be the
            # same camera — we still histogram-match them against the
            # reference, since skipping here would silently leave genuinely
            # different unlabeled cameras uncorrected.
            same_known_camera = (
                photo.camera_model is not None and photo.camera_model == reference.camera_model
            )
            if photo is reference or same_known_camera:
                baseline = ToneParams()
            else:
                if reference_image_cache is None:
                    reference_image_cache = thumbnails.get_thumbnail(reference.immich_asset_id)
                camera_image = thumbnails.get_thumbnail(photo.immich_asset_id)
                baseline = compute_camera_baseline(reference_image_cache, camera_image)

            combined = reconcile_tone(baseline, ai_delta)

            xmp_path = _xmp_path(data_root, album_slug, photo.filename)
            write_xmp_sidecar(xmp_path, combined)
        except Exception:
            # Failure isolation: one bad photo (unreadable thumbnail, disk
            # error) must not sink an entire scene/album run.
            _log.warning("failed to enhance photo %s (%s)", photo.id, photo.filename, exc_info=True)
            result.failed += 1
            continue

        photo.enhance_exposure_ev = combined.exposure_ev
        photo.enhance_temp_shift_k = combined.temp_shift_k
        photo.enhance_shadows = combined.shadows
        photo.enhance_highlights = combined.highlights
        photo.xmp_written = True
        result.enhanced += 1


__all__ = [
    "EnhanceResult",
    "ThumbnailSource",
    "ToneParams",
    "compute_camera_baseline",
    "enhance",
    "ensure_analysis",
    "reconcile_tone",
    "write_xmp_sidecar",
]
