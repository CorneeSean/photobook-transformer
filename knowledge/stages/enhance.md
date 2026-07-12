---
type: Pipeline Stage
title: enhance
description: AI estimates bounded tone parameters; classical code applies them via XMP. Cross-camera consistency folded in.
tags: [stage, ai, enhance, xmp, color-consistency]
timestamp: 2026-07-12
---

# `enhance` — AI estimates parameters; classical code applies them

Core principle: **AI as parameter estimator, never as pixel generator.** No
invented detail.

1. Ensure the **shared analysis call** (see the "One combined vision call" note
   in [Architecture](/architecture.md)) has run for each *selected* photo — one
   agent, one call per photo, producing bounded numeric deltas (exposure EV,
   white-balance shift, shadows, highlights) *alongside* the caption, from a
   single downscaled thumbnail. Bounds enforced by Pydantic
   (`Field(ge=..., le=...)`), so a bad model call can be slightly wrong but never
   drastic. `enhance` consumes the tone deltas; the caption it also produced is
   left for the [`caption`](./caption.md) stage to read.
2. **Reconcile with cross-camera consistency** (below) into a final parameter set
   per photo.
3. Write the result as **XMP sidecar files** — the metadata format
   darktable/Lightroom/Capture One natively read.
4. Hand off to [`render`](./render.md), which applies them through a mature,
   deterministic tone-mapping pipeline. We never reimplement tone-mapping math
   ourselves.

**Cross-camera color consistency is folded into this stage**, not a separate
post-pass. (Histogram-matching *after* rendering would partially undo the
per-photo corrections — they must be reconciled *before* rendering, not stacked.)
Approach: pick a reference photo per scene, compute a histogram-match offset
(`skimage.exposure.match_histograms`) as a *baseline* per-camera correction, then
apply the AI's per-photo deltas on top. Different cameras land on a common footing
first; individual exposure mistakes get fixed second.
