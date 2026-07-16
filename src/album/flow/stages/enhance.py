"""``enhance`` — AI estimates bounded tone parameters; classical code applies
them via XMP sidecars, scoped by ``album_id``.

Two responsibilities, per [enhance.md](/stages/enhance.md):

1. **Trigger the shared vision call** via the injected
   :class:`~album.flow.photo_analyzer.PhotoAnalyzer` — the "one combined vision call"
   design in [architecture.md](/foundations/architecture.md): a single agent call per
   selected photo produces bounded tone deltas *and* a caption together, because
   uploading the thumbnail is the expensive part. This stage and ``caption`` both
   receive the same kind of analyzer and call its idempotent ``ensure`` (which
   guarantees exactly one round-trip per photo against the DB), so neither
   reimplements that no-op check.
2. **Reconcile cross-camera color consistency** (:func:`compute_camera_baseline`
   + :func:`reconcile_tone`) and write the final per-photo parameters as **XMP
   sidecar files** that [`render`](./render.py) (darktable-cli) will read.

XMP schema choice
------------------
We write **darktable's real history stack** in the sidecar (the ``darktable:``
namespace), because ``render`` invokes ``darktable-cli`` directly (tech-stack.md)
and darktable reads *only* that format. darktable does not have plain-text tone
attributes: each module's settings are a **hex-encoded dump of its C params
struct** inside a ``<darktable:history>`` ``rdf:Seq``. (An earlier flat-attribute
schema here — ``darktable:exposure="0.5"`` and friends — was silently *ignored*
by darktable-cli, so ``enhance`` applied nothing at all through ``render``; this
was caught by step 22's darktable integration test and is why the format is now
the real stack. See stages/render.md.)

We emit the **minimal, backward-compatible scaffold** darktable's XMP reader
accepts: ``darktable:xmp_version="4"`` and one ``rdf:li`` history entry per
*non-zero* tone knob, with **no explicit** ``iop_order_list`` so darktable places
each module using its own built-in order for whatever version is installed (more
robust across darktable upgrades than pinning a module list). The four bounded
``ToneParams`` knobs map onto three real darktable modules:

- ``exposure_ev``           -> the ``exposure`` module's ``exposure`` field (both in EV, 1:1);
- ``shadows`` / ``highlights`` -> the ``shadhi`` module (our ``[-1, 1]`` scaled to
  darktable's ``[-100, 100]``);
- ``temp_shift_k``          -> the ``temperature`` module's red/blue channel
  multipliers (Kelvin approximated as a bounded ``±`` swing; positive = warmer).

The module ``modversion``/struct layouts are darktable 5.0.1's (the binary is
pinned in the app Docker image, build step 1); a darktable major upgrade can
change a struct and would need these updated (the integration test guards it).

Sidecar path: ``<data_root>/<album.slug>/xmp/<photo.filename>.xmp`` (e.g.
``data/albums/honeymoon-2026/xmp/IMG_0001.jpg.xmp``), mirroring
``data/albums/<slug>/rendered/`` that `render` already writes to
(folder-structure.md). ``data_root`` defaults to ``data/albums`` but is an
injectable parameter so tests never touch the real filesystem.

:class:`EnhanceStage` itself is Prefect-free logic (folder-structure.md rule) —
its ``run`` takes ``album_id`` first and is unit-testable without an orchestrator —
while this module also exports :func:`enhance_task`, the co-located Prefect
``@task`` wrapping it (quality-practices.md §1.5). Idempotent: skips photos whose
``xmp_written`` is already ``True`` unless ``force=True``; per-photo failures are
isolated (try/except + log + continue), consistent with ``quality.py``.
"""

from __future__ import annotations

import logging
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from prefect import task
from skimage.exposure import match_histograms
from sqlalchemy import select
from sqlalchemy.orm import Session

from album.agents.analysis import AnalysisResult
from album.db.models import Album, Photo
from album.flow._tasks import owns_session
from album.flow.photo_analyzer import PhotoAnalyzer, ThumbnailSource
from album.flow.stages.base import Stage
from album.flow.tone import ToneParams

_log = logging.getLogger(__name__)

_DEFAULT_DATA_ROOT = Path("data/albums")

# darktable's XMP namespaces (its real, documented sidecar schema).
_DARKTABLE_NS = "http://darktable.sf.net/xmp/1.0/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_X_NS = "adobe:ns:meta/"

_NAMESPACES = {"darktable": _DARKTABLE_NS, "rdf": _RDF_NS, "x": _X_NS}

# darktable 5.0.1 module-introspection versions (its ``DT_MODULE_INTROSPECTION``);
# darktable rejects/legacy-converts a history entry whose modversion+struct don't
# match the installed module, so these are pinned to the app image's darktable.
_MODVERSION = {"exposure": 6, "shadhi": 5, "temperature": 4}

# The default "blending off" blendop params — identical across modules for a given
# blendop version, copied verbatim from a darktable-written sidecar. Every history
# entry needs a blendop, and this one is a pass-through (the module applies fully).
_BLENDOP_VERSION = 11
_BLENDOP_OFF = "gz10eJxjYGBgYAFiCQYYOOHEgAYY0QVwggZ7CB6pfOygYtaVAyCMi08IAAB5xiOg"

# Kelvin -> red/blue channel-multiplier swing. temp_shift_k is clamped to
# ±2000 K (AnalysisResult); at that bound this yields a ~±0.15 multiplier — a
# clearly visible but bounded warm/cool shift (AGENTS.md rule 5, never drastic).
_TEMP_K_TO_MULT = 7.5e-5


@dataclass
class EnhanceResult:
    enhanced: int = 0
    skipped: int = 0
    failed: int = 0
    # selected photos with no scene_id (cluster.py's out_of_range/undated
    # photos — see cluster.md) — flagged rather than silently dropped, mirroring
    # cluster.py's own ClusterResult.out_of_range/undated pattern.
    unclustered: int = 0


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


def _exposure_params(exposure_ev: float) -> str:
    """Hex dump of ``dt_iop_exposure_params_t`` (modversion 6): manual mode, no
    black point, ``exposure`` in EV, then the two deflicker defaults and the
    ``compensate_exposure_bias`` flag."""
    return struct.pack("<iffffi", 0, 0.0, exposure_ev, 50.0, -4.0, 0).hex()


def _shadhi_params(shadows: float, highlights: float) -> str:
    """Hex dump of ``dt_iop_shadhi_params_t`` (modversion 5). Our ``[-1, 1]``
    shadows/highlights scale onto darktable's ``[-100, 100]``; the rest are the
    module's own defaults (bilateral soften, radius 100, compress 50)."""
    return struct.pack(
        "<IffffffffIfI",
        0,  # order = DT_IOP_GAUSSIAN_ZERO
        100.0,  # radius
        shadows * 100.0,  # shadows: [-1, 1] -> [-100, 100]
        0.0,  # whitepoint
        highlights * 100.0,  # highlights: [-1, 1] -> [-100, 100]
        0.0,  # reserved2
        50.0,  # compress
        100.0,  # shadows_ccorrect
        50.0,  # highlights_ccorrect
        0,  # flags
        0.000001,  # low_approximation
        1,  # shadhi_algo = SHADHI_ALGO_BILATERAL
    ).hex()


def _temperature_params(temp_shift_k: float) -> str:
    """Hex dump of ``dt_iop_temperature_params_t`` (modversion 4): red/green/blue/
    various channel multipliers + preset. A Kelvin shift is approximated as an
    equal-and-opposite red/blue swing (green fixed) — positive is warmer."""
    swing = temp_shift_k * _TEMP_K_TO_MULT
    return struct.pack("<ffffi", 1.0 + swing, 1.0, 1.0 - swing, 1.0, 0).hex()


def _history_entries(tone: ToneParams) -> list[tuple[str, str]]:
    """``(operation, params_hex)`` for each *non-zero* tone knob — a zero knob is
    a true no-op, so it's left out to keep the sidecar minimal (an all-zero tone
    yields an empty history, which darktable renders with its defaults)."""
    entries: list[tuple[str, str]] = []
    if tone.exposure_ev != 0.0:
        entries.append(("exposure", _exposure_params(tone.exposure_ev)))
    if tone.temp_shift_k != 0.0:
        entries.append(("temperature", _temperature_params(tone.temp_shift_k)))
    if tone.shadows != 0.0 or tone.highlights != 0.0:
        entries.append(("shadhi", _shadhi_params(tone.shadows, tone.highlights)))
    return entries


def write_xmp_sidecar(path: Path, tone: ToneParams) -> None:
    """Write ``tone`` as a darktable history-stack XMP sidecar at ``path``.

    See the module docstring for the schema (darktable's real binary history
    stack — the only format ``darktable-cli`` actually applies). Creates parent
    directories as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    ET.register_namespace("x", _X_NS)
    ET.register_namespace("rdf", _RDF_NS)
    ET.register_namespace("darktable", _DARKTABLE_NS)

    entries = _history_entries(tone)

    xmpmeta = ET.Element(f"{{{_X_NS}}}xmpmeta")
    rdf = ET.SubElement(xmpmeta, f"{{{_RDF_NS}}}RDF")
    description = ET.SubElement(rdf, f"{{{_RDF_NS}}}Description")
    description.set(f"{{{_RDF_NS}}}about", "")
    description.set(f"{{{_DARKTABLE_NS}}}xmp_version", "4")
    description.set(f"{{{_DARKTABLE_NS}}}history_end", str(len(entries)))

    seq = ET.SubElement(
        ET.SubElement(description, f"{{{_DARKTABLE_NS}}}history"), f"{{{_RDF_NS}}}Seq"
    )
    for num, (operation, params) in enumerate(entries):
        _append_history_entry(seq, num, operation, params)

    ET.ElementTree(xmpmeta).write(path, encoding="UTF-8", xml_declaration=True)


def _append_history_entry(seq: ET.Element, num: int, operation: str, params: str) -> None:
    """Append one ``<rdf:li>`` module entry (a fully-specified, blending-off
    history item) to the history ``rdf:Seq``."""
    li = ET.SubElement(seq, f"{{{_RDF_NS}}}li")
    attrs = {
        "num": str(num),
        "operation": operation,
        "enabled": "1",
        "modversion": str(_MODVERSION[operation]),
        "params": params,
        "multi_name": "",
        "multi_priority": "0",
        "blendop_version": str(_BLENDOP_VERSION),
        "blendop_params": _BLENDOP_OFF,
    }
    for key, value in attrs.items():
        li.set(f"{{{_DARKTABLE_NS}}}{key}", value)


def _xmp_path(data_root: Path, album_slug: str, filename: str) -> Path:
    return data_root / album_slug / "xmp" / f"{filename}.xmp"


class EnhanceStage(Stage[EnhanceResult]):
    """`enhance` — reconcile cross-camera tone and write XMP sidecars. Idempotent.

    Two collaborators, injected: the shared :class:`~album.flow.photo_analyzer.PhotoAnalyzer`
    (the one combined vision call — see architecture.md) and a
    :class:`~album.flow.photo_analyzer.ThumbnailSource`, which this stage *also* needs
    directly for the classical histogram-match baseline (not just via the analyzer).
    ``data_root`` is construction-time config; ``run`` takes ``album_id`` first.
    """

    def __init__(
        self,
        analyzer: PhotoAnalyzer,
        thumbnails: ThumbnailSource,
        *,
        data_root: Path = _DEFAULT_DATA_ROOT,
    ) -> None:
        self._analyzer = analyzer
        self._thumbnails = thumbnails
        self._data_root = data_root

    def _process(self, album: Album, session: Session, *, force: bool) -> EnhanceResult:
        """Enhance every ``selected`` photo in ``album``: ensure the shared
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
        photos = list(
            session.scalars(
                select(Photo)
                .where(Photo.album_id == album.id, Photo.selected == True)  # noqa: E712
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
            self._enhance_scene(by_scene[scene_id], session, album.slug, force, result)

        session.flush()
        return result

    def _enhance_scene(
        self,
        scene_photos: list[Photo],
        session: Session,
        album_slug: str,
        force: bool,
        result: EnhanceResult,
    ) -> None:
        reference = min(scene_photos, key=lambda p: (p.timestamp is None, p.timestamp, p.id))
        reference_image: bytes | None = None

        for photo in scene_photos:
            if photo.xmp_written and not force:
                result.skipped += 1
                continue

            try:
                analysis = self._analyzer.ensure(session, photo, force=force)
                ai_delta = self._ai_delta(photo, analysis)
                baseline, reference_image = self._baseline(photo, reference, reference_image)
                combined = reconcile_tone(baseline, ai_delta)
                write_xmp_sidecar(_xmp_path(self._data_root, album_slug, photo.filename), combined)
            except Exception:
                # Failure isolation: one bad photo (unreadable thumbnail, disk
                # error) must not sink an entire scene/album run.
                _log.warning(
                    "failed to enhance photo %s (%s)", photo.id, photo.filename, exc_info=True
                )
                result.failed += 1
                continue

            combined.write_to(photo)
            photo.xmp_written = True
            result.enhanced += 1

    @staticmethod
    def _ai_delta(photo: Photo, analysis: AnalysisResult | None) -> ToneParams:
        """The raw per-photo AI tone delta, from a fresh analysis or the stored columns."""
        if analysis is not None:
            return ToneParams.from_analysis(analysis)
        # analysis is None only when force=False and the photo was already analyzed
        # but never reconciled/xmp_written (e.g. a previous run crashed between the
        # analysis call and the XMP write) — the stored columns still hold the
        # untouched raw AI delta.
        return ToneParams.from_photo(photo)

    def _baseline(
        self, photo: Photo, reference: Photo, reference_image: bytes | None
    ) -> tuple[ToneParams, bytes | None]:
        """The cross-camera baseline offset, plus the (lazily fetched, reused)
        reference thumbnail so a scene fetches it at most once.

        Only treat a photo as "same camera as reference, no baseline needed" when
        ``camera_model`` is a known, matching, non-null value. Two photos both
        missing EXIF camera_model (common for screenshots or re-compressed shares)
        are *not* assumed to be the same camera — they're still histogram-matched,
        since skipping would silently leave genuinely different unlabeled cameras
        uncorrected.
        """
        same_known_camera = (
            photo.camera_model is not None and photo.camera_model == reference.camera_model
        )
        if photo is reference or same_known_camera:
            return ToneParams(), reference_image
        if reference_image is None:
            reference_image = self._thumbnails.get_thumbnail(reference.immich_asset_id)
        camera_image = self._thumbnails.get_thumbnail(photo.immich_asset_id)
        return compute_camera_baseline(reference_image, camera_image), reference_image


@task(name="enhance")
@owns_session
def enhance_task(stage: EnhanceStage, album_id: int, session: Session) -> None:
    """``enhance`` as a Prefect ``@task`` on its own pooled session (``owns_session``)."""
    stage.run(album_id, session)


__all__ = [
    "EnhanceResult",
    "EnhanceStage",
    "compute_camera_baseline",
    "enhance_task",
    "reconcile_tone",
    "write_xmp_sidecar",
]
