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
- **Learned:** LAION aesthetic predictor — a small pretrained linear head over
  CLIP embeddings, so nearly free once embeddings exist.

Pull thumbnails from the Immich API rather than re-reading originals. If Immich
doesn't expose its own embeddings, this stage also computes the
[`clip_embedding`](/reference/data-model.md) (via `open_clip`) that
[`cluster`](./cluster.md) and [`select`](./select.md) depend on.
