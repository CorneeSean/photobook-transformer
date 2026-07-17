"""Small OpenCV decode helpers shared by the classical image analysis code.

Both the classical tone estimator ([`tone_estimator.py`](./tone_estimator.py)) and
``enhance``'s cross-camera baseline ([`stages/enhance.py`](../flow/stages/enhance.py))
decode raw thumbnail bytes to a float array before measuring luminance/colour; this
is the one place that decode lives so the two agree byte-for-byte on it (the module
is ``_``-prefixed to mark it package-internal).
"""

from __future__ import annotations

import cv2
import numpy as np


def decode_gray(image: bytes) -> np.ndarray:
    """Decode ``image`` to a single-channel float64 luminance array."""
    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise ValueError("could not decode image bytes")
    return decoded.astype(np.float64)


def decode_color(image: bytes) -> np.ndarray:
    """Decode ``image`` to an ``H×W×3`` float64 BGR array (OpenCV channel order)."""
    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("could not decode image bytes")
    return decoded.astype(np.float64)


__all__ = ["decode_color", "decode_gray"]
