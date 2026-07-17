"""darktable render integration test — TestContainers playbook step 22.

Where ``test_render.py`` covers :class:`~album.flow.stages.render.RenderStage`'s
orchestration with an injected fake :class:`~album.flow.stages.render.Renderer`, this
runs the **real** :class:`~album.flow.stages.render.DarktableRenderer` (which shells
out to ``darktable-cli``) **inside the shipped app Docker image**, over a real
source JPEG and a real :func:`~album.flow.stages.enhance.write_xmp_sidecar` sidecar,
and asserts the rendered JPEG exists, decodes, and is **visibly brighter** than
the source.

This is the automated form of build step 11's deferred *"a rendered file exists,
opens, and is visibly corrected versus its original"* manual check — no
internet-hosted model, just the ``darktable-cli`` binary the app image already
ships. It is also the guard that keeps ``enhance``'s sidecar format *actually
applying* in darktable: the previous flat-attribute schema silently applied
nothing (a no-op render), which this test would now catch as "not brighter"
(see stages/render.md / enhance.py's XMP-schema note).

Needs Docker and **builds the app image** (slow on first run, layer-cached
after). Marked ``integration``, so ``poe test-unit`` skips it. It shells out to
the ``docker`` CLI rather than the docker-py SDK on purpose: this sandbox's
``~/.docker`` points at a ``docker-credential-gcloud`` helper that crashes under
Python 3.14 (``No module named 'imp'``), which breaks docker-py's image build —
the CLI resolves credentials fine (progress.md landmines).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from album.analysis.tone import ToneParams
from album.flow.stages.enhance import write_xmp_sidecar

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_IMAGE_TAG = "album-render-integration:latest"

# Runs the real DarktableRenderer inside the container — the same code path
# `render` uses in production (subprocess -> darktable-cli), against the real
# darktable binary baked into the app image.
_RENDER_SCRIPT = (
    "from pathlib import Path;"
    "from album.flow.stages.render import DarktableRenderer;"
    "DarktableRenderer().render("
    "Path('/work/in.jpg'), Path('/work/in.jpg.xmp'), Path('/work/out.jpg'))"
)


@pytest.fixture(scope="session")
def app_image() -> str:
    """Build the shipped app Docker image (darktable-cli + the album package)
    once per session; returns its tag. The build is idempotent and layer-cached,
    so a warm run is fast; the image is left in place as that cache."""
    build = subprocess.run(
        ["docker", "build", "-t", _IMAGE_TAG, str(_REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.fail(f"app image build failed:\n{build.stdout}\n{build.stderr}")
    return _IMAGE_TAG


def _run_render(image: str, work_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{work_dir}:/work",
            "-e",
            "HOME=/tmp",  # darktable-cli needs a writable config dir
            "--entrypoint",
            "python",
            image,
            "-c",
            _RENDER_SCRIPT,
        ],
        capture_output=True,
        text=True,
    )


def _write_source_jpeg(path: Path) -> float:
    """A neutral horizontal gradient JPEG (so an exposure lift is unambiguous in
    the mean luminance); returns its mean luma."""
    ramp = np.tile(np.linspace(40, 160, 256, dtype=np.uint8), (256, 1))
    Image.fromarray(np.stack([ramp, ramp, ramp], axis=-1), "RGB").save(path, "JPEG", quality=95)
    return float(np.asarray(Image.open(path).convert("L")).mean())


def test_darktable_renders_visibly_corrected_jpeg(app_image: str, tmp_path: Path) -> None:
    source_luma = _write_source_jpeg(tmp_path / "in.jpg")

    # The real enhance sidecar, with a strong but bounded positive correction.
    write_xmp_sidecar(
        tmp_path / "in.jpg.xmp",
        ToneParams(exposure_ev=2.0, shadows=0.3, highlights=-0.3),
    )

    result = _run_render(app_image, tmp_path)
    assert result.returncode == 0, f"render failed:\n{result.stdout}\n{result.stderr}"

    out = tmp_path / "out.jpg"
    assert out.exists(), f"render produced no output.\n{result.stdout}\n{result.stderr}"

    rendered = Image.open(out)
    rendered.load()  # forces a full decode — raises if the JPEG is corrupt
    assert rendered.size == (256, 256)

    # darktable actually applied the +2 EV sidecar: a silently-ignored sidecar
    # (the old flat-attribute bug) would re-encode at ~the same luminance.
    rendered_luma = float(np.asarray(rendered.convert("L")).mean())
    assert rendered_luma > source_luma * 1.3, (
        f"rendered luma {rendered_luma:.1f} not brighter than source {source_luma:.1f} — "
        "darktable did not apply the sidecar's exposure correction"
    )
