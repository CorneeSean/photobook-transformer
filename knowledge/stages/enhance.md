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
3. Write the result as **XMP sidecar files** — non-destructive metadata that
   [`render`](./render.md) applies on demand (tiny per-photo file, no second
   pixel copy stored).
4. Hand off to [`render`](./render.md), which applies them through a mature,
   deterministic tone-mapping pipeline. We never reimplement tone-mapping math
   ourselves.

### XMP sidecar format (darktable's real history stack)

darktable applies edits **only** from its binary *history stack*: each module's
settings are a hex-encoded dump of its C params struct inside a
`<darktable:history>` `rdf:Seq`. It has no plain-text tone attributes. `enhance`
writes the minimal, backward-compatible scaffold darktable accepts
(`darktable:xmp_version="4"`, no explicit `iop_order_list` so darktable uses its
own module order), with one history entry per *non-zero* tone knob mapped to a
real module:

| `ToneParams` knob | darktable module | mapping |
|---|---|---|
| `exposure_ev` | `exposure` | 1:1 (both EV) |
| `shadows` / `highlights` | `shadhi` | our `[-1, 1]` → darktable's `[-100, 100]` |
| `temp_shift_k` | `temperature` | Kelvin → bounded red/blue multiplier swing (positive = warmer) |

The module `modversion`s + struct layouts are **pinned to the app image's
darktable (5.0.1)**; a darktable major upgrade can change a struct and would need
these updated. `tests/test_render_integration.py` (step 22) is the guard — it
renders a real sidecar through the real binary and asserts a visible change.

> **History note:** an earlier version wrote flat `darktable:exposure="0.5"`
> attributes, which `darktable-cli` **silently ignored** — `enhance` corrections
> never reached the rendered image. Caught by step 22's integration test; see
> [render.md](./render.md).

**Cross-camera color consistency is folded into this stage**, not a separate
post-pass. (Histogram-matching *after* rendering would partially undo the
per-photo corrections — they must be reconciled *before* rendering, not stacked.)
Approach: pick a reference photo per scene, compute a histogram-match offset
(`skimage.exposure.match_histograms`) as a *baseline* per-camera correction, then
apply the AI's per-photo deltas on top. Different cameras land on a common footing
first; individual exposure mistakes get fixed second.
