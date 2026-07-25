"""``analysis/aesthetic.py`` — the real LAION-style aesthetic scorer + CLIP embedder.

Wires the heavy ML that :class:`~album.flow._flow._UnavailableQualityModel` stood in
for (build step 31). :class:`ClipAestheticModel` backs **both** the
:class:`~album.flow.stages.quality.AestheticScorer` and
:class:`~album.flow.stages.quality.Embedder` protocols from a single loaded model —
OpenAI CLIP ViT-L/14's image encoder produces the 768-dim embedding, and the LAION
aesthetic head is a small MLP on top of that same feature, so one CLIP forward serves
both. That's the same reasoning ``_UnavailableQualityModel`` follows, and it's why the
flow injects one instance as both collaborators.

Homed in ``analysis/`` alongside the other heavy/local ML (``tone_estimator``,
``captioner``) and kept **Prefect-free** (folder-structure.md rule #4). ``torch`` and
``open_clip`` are lazy-imported **inside methods** (the Immich-client pattern), so:

- the module imports without the ``scoring`` extra installed (collection / basedpyright
  don't hard-require torch), and
- constructing the model is cheap: :func:`_require_capability` fails fast via
  ``importlib.util.find_spec`` + a weights-file check **without importing torch**, so
  :func:`~album.flow._flow._build_quality_model`'s graceful fallback (and the wiring
  tests) never pay the ~1.7 GB load. The heavy load happens lazily on the first
  ``score``/``embed`` call.

**`torch`/`open_clip` are an OPTIONAL extra, so basedpyright can't hard-require them.**
This one module gets a file-scoped ``reportMissingImports=false`` (a single-rule
override, *not* a blanket ``# pyright: ignore``) so that a plain ``uv sync`` (which
omits the ``scoring`` extra) still passes ``poe check`` — the whole point of keeping
torch opt-in. When a dev/CI runs ``uv sync --extra scoring`` the imports resolve and
this module is fully type-checked as normal; the override only relaxes *missing-import*
resolution for torch/open_clip, nothing else.
"""

# pyright: reportMissingImports=false

from __future__ import annotations

import hashlib
import io
import logging
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from album.config import QualitySettings, get_settings

if TYPE_CHECKING:
    from torch import Tensor
    from torch import device as TorchDevice
    from torch.nn import Module

# open_clip's model + image transform are third-party dynamic objects with no reliable
# stubs; typed ``Any`` so the (correct) attribute calls on them don't fight the stubs.
# The tensors and device that flow through our own code stay precisely typed.
_OpenClipModel = Any
_Preprocess = Any

_log = logging.getLogger(__name__)

_EMBED_DIM = 768

# Provenance + licenses (web-verified 2026-07-18) — both permissive, no blocker:
# - CLIP backbone: OpenAI CLIP ViT-L/14 via open_clip (model="ViT-L-14-quickgelu",
#   pretrained="openai" — QuickGELU matches how the OpenAI weights + the aesthetic head
#   were trained) — 768-dim image embedding, matching models.py's Vector(768).
#   License MIT (the open_clip library and the OpenAI CLIP weights are both MIT).
#   https://github.com/mlfoundations/open_clip
#   https://huggingface.co/openai/clip-vit-large-patch14
# - Aesthetic head: christophschuhmann/improved-aesthetic-predictor, weights
#   sac+logos+ava1-l14-linearMSE.pth — a small MLP over the same 768-dim features.
#   Repo license Apache-2.0.
#   https://github.com/christophschuhmann/improved-aesthetic-predictor
_AESTHETIC_HEAD_URL = (
    "https://github.com/christophschuhmann/improved-aesthetic-predictor/"
    "raw/main/sac+logos+ava1-l14-linearMSE.pth"
)

# open_clip attributes that make up the text tower — dropped after load, since the
# embedder + aesthetic head only need the visual tower (saves ~0.5 GB steady-state).
_TEXT_TOWER_ATTRS = (
    "transformer",
    "token_embedding",
    "ln_final",
    "text_projection",
    "positional_embedding",
    "text",
)

# The torch devices this adapter accepts. Actual availability of mps/cuda is only
# knowable once torch is imported (at load time); this cheap set-membership check just
# rejects an outright-bogus `QUALITY__DEVICE` at construction, so a typo degrades to
# the placeholder via one clean build-time warning rather than a per-photo failure.
_SUPPORTED_DEVICES = frozenset({"cpu", "mps", "cuda"})


def _weights_path(settings: QualitySettings) -> Path:
    """The on-disk path of the LAION aesthetic head — the one place this is derived from
    config, so the requirement check, the model, and the fetch task can't drift."""
    return Path(settings.weights_dir) / settings.aesthetic_weights


def _check_dim(values: list[float]) -> list[float]:
    """Fail loud on shape drift: a wrong-length embedding means a wrong model/pretrained
    pairing, and must surface here rather than as an opaque pgvector insert error."""
    if len(values) != _EMBED_DIM:
        raise ValueError(f"expected a {_EMBED_DIM}-dim CLIP embedding, got {len(values)}")
    return values


def _require_capability(settings: QualitySettings) -> None:
    """Cheap, torch-free fail-fast for the ways the real scorer can't run — the
    ``scoring`` extra absent, an unsupported device, an impossible device/precision
    combo, or the weights not fetched. :func:`~album.flow._flow._build_quality_model`
    catches each and degrades to the placeholder (with a loud warning), so a bad config
    yields one build-time warning rather than a failure on every photo."""
    _require_scoring_extra()
    _require_supported_device(settings)
    _require_usable_precision(settings)
    _require_weights(settings)


def _require_scoring_extra() -> None:
    missing = [name for name in ("torch", "open_clip") if find_spec(name) is None]
    if missing:
        raise ImportError(
            f"the 'scoring' extra is not installed (missing {', '.join(missing)}); run "
            "`uv sync --extra scoring` or `pip install album[scoring]`",
        )


def _require_supported_device(settings: QualitySettings) -> None:
    if settings.device not in _SUPPORTED_DEVICES:
        raise ValueError(
            f"unsupported quality.device {settings.device!r}; "
            f"choose from {sorted(_SUPPORTED_DEVICES)}",
        )


def _require_usable_precision(settings: QualitySettings) -> None:
    if settings.precision == "fp16" and settings.device == "cpu":
        raise ValueError("precision='fp16' is not usable on device='cpu' — use 'mps' or 'cuda'")


def _require_weights(settings: QualitySettings) -> None:
    weights = _weights_path(settings)
    if not weights.exists():
        raise FileNotFoundError(
            f"aesthetic head weights not found at {weights}; run "
            "`uv run poe fetch-aesthetic-weights`",
        )


class ClipAestheticModel:
    """Real LAION-style aesthetic predictor + CLIP embedder (ViT-L/14, 768-dim).

    One model backs both protocols (score + embed). Cheap to construct (fail-fast only);
    lazily loads torch/open_clip + the weights on first use, then caches the last image's
    CLIP features (a one-entry memo keyed on the image bytes' hash) so a ``score`` then an
    ``embed`` on the *same* thumbnail runs the CLIP forward exactly once.
    """

    def __init__(self, settings: QualitySettings) -> None:
        _require_capability(settings)
        self._settings = settings
        self._weights_path = _weights_path(settings)
        self._model: _OpenClipModel | None = None
        self._preprocess: _Preprocess | None = None
        self._head: Module | None = None
        self._device: TorchDevice | None = None
        self._memo_key: str | None = None
        self._memo: Tensor | None = None
        # Test observability (private — not part of the public contract): how many CLIP
        # forwards actually ran, so a test can assert the memo forwards once per image.
        self._forward_count = 0

    def embed(self, image: bytes) -> list[float]:
        """The 768-dim, L2-normalised CLIP image embedding (asserted length)."""
        features = self._features(image)
        return _check_dim(features.squeeze(0).cpu().tolist())

    def score(self, image: bytes) -> float:
        """The LAION aesthetic score: the same CLIP features → the linear head."""
        import torch

        features = self._features(image)
        with torch.no_grad():
            prediction = self._require_head()(features)
        return float(prediction.squeeze().item())

    def _features(self, image: bytes) -> Tensor:
        """The L2-normalised ViT-L/14 image features for ``image``, memoised one-deep on
        the byte hash so ``score`` + ``embed`` on the same thumbnail forward once."""
        key = hashlib.sha1(image).hexdigest()
        if key == self._memo_key and self._memo is not None:
            return self._memo
        features = self._encode(image)
        self._memo_key, self._memo = key, features
        self._forward_count += 1
        return features

    def _encode(self, image: bytes) -> Tensor:
        """Decode the bytes, preprocess, and run the (lazily-loaded) visual tower."""
        import torch
        from PIL import Image

        self._ensure_loaded()
        pil = Image.open(io.BytesIO(image)).convert("RGB")
        tensor = self._to_input(pil)
        with torch.no_grad():
            features = self._require_model().encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
        return features.float()  # the fp32 head consumes these even under fp16 CLIP

    def _to_input(self, pil: object) -> Tensor:
        """Preprocess a PIL image to the model's input tensor, on the right device/dtype."""
        preprocess = self._preprocess
        assert preprocess is not None  # set by _ensure_loaded, called before this
        tensor = preprocess(pil).unsqueeze(0).to(self._device)
        return tensor.half() if self._settings.precision == "fp16" else tensor

    def _ensure_loaded(self) -> None:
        """Load the visual tower + aesthetic head once (idempotent)."""
        if self._model is not None:
            return
        import torch

        device = torch.device(self._settings.device)
        self._model, self._preprocess = _load_visual_backbone(self._settings, device)
        self._head = _load_head(self._weights_path, device)
        self._device = device

    # Both accessors assume a prior _ensure_loaded() — model+preprocess+head are loaded
    # together, and both public entry points ensure it first: _encode() calls
    # _ensure_loaded() before _require_model(), and score() goes through _features()
    # (which ensures on a miss; a memo hit implies a prior load) before _require_head().
    def _require_model(self) -> _OpenClipModel:
        assert self._model is not None
        return self._model

    def _require_head(self) -> Module:
        assert self._head is not None
        return self._head


def _load_visual_backbone(
    settings: QualitySettings,
    device: TorchDevice,
) -> tuple[_OpenClipModel, _Preprocess]:
    """Create the CLIP model + its image transform (one download), drop the text tower
    (~0.5 GB), and move the model to the configured device/precision."""
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        settings.clip_model,
        pretrained=settings.clip_pretrained,
        cache_dir=settings.weights_dir,
    )
    _drop_text_tower(model)
    model = model.to(device).eval()
    return (model.half() if settings.precision == "fp16" else model), preprocess


def _drop_text_tower(model: _OpenClipModel) -> None:
    """Free the unused text tower — the embedder + head need only ``model.visual``.
    Best-effort: a few attrs resist reassignment, and dropping them is an optimisation,
    not a correctness requirement, so a failure to drop one is swallowed."""
    for attr in _TEXT_TOWER_ATTRS:
        if hasattr(model, attr):
            try:
                setattr(model, attr, None)
            except Exception:  # best-effort memory trim — never fatal
                pass


def _load_head(weights_path: Path, device: TorchDevice) -> Module:
    """The LAION aesthetic head: an MLP over the 768-dim CLIP feature, weights loaded
    from ``weights_path``. Its architecture must match the state dict in
    ``sac+logos+ava1-l14-linearMSE.pth`` (the improved-aesthetic-predictor MLP)."""
    import torch
    from torch import nn

    head = nn.Sequential(
        nn.Linear(_EMBED_DIM, 1024),
        nn.Dropout(0.2),
        nn.Linear(1024, 128),
        nn.Dropout(0.2),
        nn.Linear(128, 64),
        nn.Dropout(0.1),
        nn.Linear(64, 16),
        nn.Linear(16, 1),
    )
    state = torch.load(weights_path, map_location=device, weights_only=True)
    head.load_state_dict(_strip_layers_prefix(state))
    return head.to(device).eval()


def _strip_layers_prefix(state: dict[str, Tensor]) -> dict[str, Tensor]:
    """The published head wraps its ``nn.Sequential`` in ``self.layers``, so its keys are
    ``layers.0.weight`` …; our bare ``nn.Sequential`` wants ``0.weight`` …. Strip the
    prefix so the state dict loads regardless of which shape the checkpoint was saved in."""
    prefix = "layers."
    return {key.removeprefix(prefix): value for key, value in state.items()}


def fetch_weights(settings: QualitySettings | None = None) -> None:
    """Download the ViT-L/14 open_clip weights + the LAION aesthetic head into
    ``quality.weights_dir`` so a real ``quality`` run is fully offline afterward.

    One-time and internet-touching (mirrors ``bake-immich-ml``); driven by
    ``uv run poe fetch-aesthetic-weights``. Needs the ``scoring`` extra installed.

    Provenance + licenses (web-verified 2026-07-18):

    - **CLIP backbone** — OpenAI CLIP ViT-L/14 via open_clip, license **MIT**
      (https://github.com/mlfoundations/open_clip,
      https://huggingface.co/openai/clip-vit-large-patch14). Cached by open_clip itself.
    - **Aesthetic head** — ``sac+logos+ava1-l14-linearMSE.pth`` from
      christophschuhmann/improved-aesthetic-predictor, license **Apache-2.0**
      (https://github.com/christophschuhmann/improved-aesthetic-predictor).
    """
    import open_clip

    settings = settings or get_settings().quality
    weights_dir = Path(settings.weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)

    _log.info("fetching open_clip %s/%s weights", settings.clip_model, settings.clip_pretrained)
    open_clip.create_model_and_transforms(
        settings.clip_model,
        pretrained=settings.clip_pretrained,
        cache_dir=str(weights_dir),
    )

    head_path = _weights_path(settings)
    if head_path.exists():
        _log.info("aesthetic head already present at %s", head_path)
        return
    _log.info("downloading aesthetic head → %s", head_path)
    _download(_AESTHETIC_HEAD_URL, head_path)


def _download(url: str, destination: Path) -> None:
    """Stream ``url`` to ``destination`` (written atomically via a temp sibling; the
    partial ``.part`` is cleaned up if the stream fails mid-download)."""
    tmp = destination.with_suffix(destination.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
            response.raise_for_status()
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        tmp.replace(destination)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


__all__ = ["ClipAestheticModel", "fetch_weights"]
