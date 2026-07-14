"""``render`` — ``darktable-cli`` batch invocation over the XMP sidecars
written by [`enhance`](./enhance.py), scoped by ``album_id``.

Per [render.md](/stages/render.md): not meaningfully unit-testable at the
"does darktable actually produce a corrected JPEG" level (working-agreement.md
puts darktable render, Docker, and Lulu submission in that bucket) —
**manually verified** instead (build step 11). What *is* unit-tested here is
the orchestration around it, mirroring how ``quality.py``/``enhance.py`` keep
their external dependencies behind an injectable :class:`typing.Protocol`
rather than hitting the real thing in tests:

- fetching the photo's full-resolution original via the injected
  :class:`OriginalSource` (:class:`~album.immich.client.ImmichClient`'s new
  ``get_original`` satisfies it) — XMP sidecars carry only edit instructions,
  not pixels, so darktable-cli needs the source file too;
- invoking the injected :class:`Renderer` (the real implementation shells out
  to ``darktable-cli`` via ``subprocess.run``, checking the return code) with
  the right ``(input_path, xmp_path, output_path)`` arguments;
- idempotency (``rendered_path`` already set -> skip, unless ``force``);
- a selected-but-not-yet-enhanced photo (``xmp_written`` still ``False``) is
  counted/logged distinctly rather than silently dropped or errored, mirroring
  ``enhance.py``'s own handling of selected-but-unclustered photos;
- per-photo failure isolation (try/except + log + continue), consistent with
  ``quality.py``/``enhance.py``.

Sidecar path convention matches ``enhance.py``:
``<data_root>/<album.slug>/xmp/<photo.filename>.xmp``. Output JPEGs go to
``<data_root>/<album.slug>/rendered/<photo.filename>`` (folder-structure.md).
The fetched original is staged at
``<data_root>/<album.slug>/originals/<photo.filename>`` as darktable-cli's
input file — it needs a real file on disk, not bytes in memory. That staged
copy is transient scratch space, not a documented data directory: it's reused
across retries within a run (skips the fetch if already present) and deleted
once its render succeeds, so full-resolution originals don't permanently
double an album's disk footprint alongside the real artifact (the rendered
JPEG). A failed render intentionally leaves its staged original in place, so
a retry doesn't re-fetch it.

No Prefect imports (folder-structure.md rule). ``album_id`` is always the
first argument.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from album.db.models import Album, Photo

_log = logging.getLogger(__name__)

_DEFAULT_DATA_ROOT = Path("data/albums")


class OriginalSource(Protocol):
    def get_original(self, asset_id: str) -> bytes: ...


# runtime_checkable: flow.py's pipeline() (a Prefect @flow) takes this type
# directly in its own signature. Prefect always builds a Pydantic parameter
# schema for a flow's signature (even with validate_parameters=False, which
# pipeline() sets — that only skips *validating* args against the schema,
# not building it), and that schema generation does an isinstance check on
# every annotation. (@task has no such schema/validation step at all.)
@runtime_checkable
class Renderer(Protocol):
    """Invokes darktable-cli (or a stand-in, in tests) over one photo.

    Must raise on failure (a non-zero return code from darktable-cli is a
    failure, not a silent success) — the caller isolates the exception
    per-photo, exactly like a raised exception from any other step.
    """

    def render(self, input_path: Path, xmp_path: Path, output_path: Path) -> None: ...


class DarktableRenderer:
    """The real :class:`Renderer`: shells out to ``darktable-cli``.

    Not exercised by the unit tests (no ``darktable-cli`` binary on this host
    or in CI — it lives only in the Docker app image, see
    folder-structure.md/build-order.md step 1); covered instead by build step
    11's manual verification.
    """

    def render(self, input_path: Path, xmp_path: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(  # noqa: S603
            ["darktable-cli", str(input_path), str(xmp_path), str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"darktable-cli exited {completed.returncode} for {input_path}: "
                f"{completed.stderr.strip()}"
            )


@dataclass
class RenderResult:
    rendered: int = 0
    skipped: int = 0
    failed: int = 0
    # selected photos whose enhance/XMP step hasn't run yet (xmp_written is
    # still False) — flagged rather than silently dropped, mirroring
    # EnhanceResult.unclustered's pattern for cluster.py's own out-of-band case.
    not_ready: int = 0


def _originals_path(data_root: Path, album_slug: str, filename: str) -> Path:
    return data_root / album_slug / "originals" / filename


def _xmp_path(data_root: Path, album_slug: str, filename: str) -> Path:
    return data_root / album_slug / "xmp" / f"{filename}.xmp"


def _rendered_path(data_root: Path, album_slug: str, filename: str) -> Path:
    return data_root / album_slug / "rendered" / filename


def render(
    album_id: int,
    session: Session,
    originals: OriginalSource,
    renderer: Renderer,
    *,
    data_root: Path = _DEFAULT_DATA_ROOT,
    force: bool = False,
) -> RenderResult:
    """Render every ``selected`` photo in ``album_id`` that has an XMP
    sidecar (``xmp_written=True``) into a corrected JPEG via ``renderer``.
    Idempotent unless ``force``.
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

    result = RenderResult()
    for photo in photos:
        if not photo.xmp_written:
            # Selected but enhance hasn't produced a sidecar yet — nothing for
            # darktable-cli to read. Flagged, not silently dropped.
            _log.warning(
                "photo %s (%s) is selected but has no XMP sidecar yet — skipping render",
                photo.id,
                photo.filename,
            )
            result.not_ready += 1
            continue

        already_done = photo.rendered_path is not None and not force
        if already_done:
            result.skipped += 1
            continue

        try:
            input_path = _originals_path(data_root, album.slug, photo.filename)
            xmp_path = _xmp_path(data_root, album.slug, photo.filename)
            output_path = _rendered_path(data_root, album.slug, photo.filename)

            # Reuse an already-staged original (e.g. a retry after darktable-cli
            # failed downstream) rather than re-fetching it from Immich every
            # time — the fetch is the expensive, network-bound part.
            if not input_path.exists():
                input_path.parent.mkdir(parents=True, exist_ok=True)
                input_path.write_bytes(originals.get_original(photo.immich_asset_id))

            renderer.render(input_path, xmp_path, output_path)
            if not output_path.exists():
                # Renderer's contract is "raise on failure" (a non-zero
                # darktable-cli exit), but a crashed/killed process could in
                # principle exit 0 without writing the file — verify rather
                # than trust a silent success, since a bad rendered_path
                # would otherwise propagate to `assemble`.
                raise RuntimeError(f"renderer reported success but {output_path} was not created")
        except Exception:
            # Failure isolation: one bad photo (unreachable original, a
            # crashed darktable-cli invocation) must not sink the whole album.
            _log.warning("failed to render photo %s (%s)", photo.id, photo.filename, exc_info=True)
            result.failed += 1
            continue

        # The staged original was only needed as darktable-cli's input; the
        # rendered JPEG is the artifact worth keeping, so don't let a
        # full-resolution copy of every photo accumulate on disk forever.
        input_path.unlink(missing_ok=True)

        photo.rendered_path = str(output_path)
        result.rendered += 1

    session.flush()
    return result


__all__ = [
    "DarktableRenderer",
    "OriginalSource",
    "Renderer",
    "RenderResult",
    "render",
]
