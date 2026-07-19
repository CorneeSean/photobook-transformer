---
type: Pipeline Stage
title: quality
description: Score blur and aesthetics — classical metrics plus a learned predictor over CLIP embeddings.
tags: [stage, custom, quality, opencv, laion]
timestamp: 2026-07-12
---

# `quality` — custom

Immich doesn't score aesthetics, so this is ours:

- **Classical:** Laplacian variance (OpenCV) for blur; histogram checks for blown
  highlights / crushed shadows.
- **Learned:** LAION aesthetic predictor — a small pretrained MLP head over CLIP
  embeddings, so nearly free once embeddings exist.

Pull thumbnails from the Immich API rather than re-reading originals. If Immich
doesn't expose its own embeddings, this stage also computes the
[`clip_embedding`](/reference/data-model.md) (via `open_clip`) that
[`cluster`](./cluster.md) and [`select`](./select.md) depend on.

**Real scorer (build step 31).** The learned scorer + CLIP embedder are
[`ClipAestheticModel`](/build/archive/step-31-laion-aesthetic-scorer.md) (ViT-L/14 CLIP +
LAION head), injected behind the `AestheticScorer`/`Embedder` protocols — one shared
model backs both, with a one-entry feature memo so `score` + `embed` on the same
thumbnail run the CLIP forward once. It's **capability-gated**: `config.quality.scorer`
defaults to `"clip"`, but the model needs the opt-in **`scoring` extra** (`uv sync
--extra scoring`, torch + open_clip kept out of base deps) plus fetched weights
(`uv run poe fetch-aesthetic-weights`). If either is absent the flow logs a loud
warning and falls back to the NULL-scoring placeholder, so a base install still runs
(`select` has a tested fallback for NULL scores); `scorer="none"` opts out explicitly.
The model is local, so it doesn't break the offline-by-default posture — the extra is
opt-in only because torch is heavy (~1.7 GB), not for connectivity.
