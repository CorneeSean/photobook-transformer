---
type: Pipeline Stage
title: caption
description: Grounded captions from the shared analysis call — as grounded as the album's metadata allows.
tags: [stage, ai, caption, grounding]
timestamp: 2026-07-12
---

# `caption`

Consumes the caption produced by the **shared per-photo analysis** described under
[`enhance`](./enhance.md) (one thumbnail fetch per selected photo, producing tone
deltas and a caption together). If `enhance` already ran, the caption is simply read
back; if `caption` runs first, it triggers that same idempotent analysis. Either way
the photo is analyzed **at most once** — this stage's job is to supply the grounding
context and to store the result.

**Which captioner runs is selectable per run** (step 29 —
[local-analysis.md](/build/archive/local-analysis.md)), behind a
[`Captioner`](../../src/album/analysis/captioner.py) seam:

- **`TemplateCaptioner`** — the **default**, fully offline (no API, no key): composes
  who/where/when from the structured grounding already in the DB.
- **`ClaudeCaptioner`** — opt-in "rich caption" (like `LuluPrinter`), reading pixel
  scene semantics too. Selected via `CAPTIONER=claude` / `--captioner claude`.

Pick per run to A/B the two (with `--force` to re-caption an already-run album).

**Context is whatever the album has**, so captions are as grounded as the metadata
allows:

- Segment name and place, if the album has segments → *"Late afternoon at the
  harbour in Lisbon"*
- The photo's own reverse-geocoded place (Immich `city`/`state`/`country`, joined
  most-to-least specific; enrichment step 20) → the finest location grounding below
  the segment
- The names of recognized people in the photo (enrichment step 19)
- The capture time (→ a coarse "Month YYYY")
- `Album.meta` extras (couple's names, event type) if present
- Nothing at all → still produces an accurate, if generic, caption

This is the payoff of keeping [`Segment`](/foundations/domain-model.md) structured rather than
opaque: the caption can meaningfully interpolate it.
