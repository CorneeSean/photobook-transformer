---
type: Pipeline Stage
title: dedupe
description: Immich's built-in duplicate detection; we consume the results, not our code.
tags: [stage, immich, dedupe, automatic]
timestamp: 2026-07-12
---

# `dedupe` — Immich (automatic)

Immich's built-in duplicate detection (perceptual hash + ML) groups
near-identical and burst shots. Not our code — we just consume the results (the
`duplicate_group_id` on [`Photo`](../reference/data-model.md), pulled in by
[`sync`](./sync.md) and honored by [`select`](./select.md)).
