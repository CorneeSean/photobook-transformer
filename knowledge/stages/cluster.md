---
type: Pipeline Stage
title: cluster
description: Assign photos to segments and detect scenes; degrades gracefully with no segments and no GPS.
tags: [stage, custom, cluster, segment, scene, genericity]
timestamp: 2026-07-12
---

# `cluster` — custom

Assigns each photo to a **segment** (if the album has any) and a **scene** (a
sub-segment burst of related shots). This is where the album's structure gets
imposed, and it degrades gracefully.

**Segment assignment** — match each photo's timestamp against the album's
[segments](../foundations/domain-model.md) (`start_at`/`end_at`). If the album has no segments,
every photo belongs to a single implicit segment covering the whole album. Photos
falling outside every segment are flagged, not silently dropped. Comparison is
done entirely in UTC (both sides normalized — see [Data model](../reference/data-model.md)),
so segment-edge assignment is unambiguous even across zones.

**Scene detection** within a segment, in order of preference:

1. **Spatial** (if the photo has GPS): cluster on lat/lon (e.g. DBSCAN) in
   combination with time — tightest boundaries.
2. **Temporal** (always available): a time gap over a configurable threshold
   (~90 min default) starts a new scene. This is the workhorse.
3. **Visual refinement** (optional): CLIP-embedding similarity splits scenes
   where two visually different photos were taken minutes apart.

Crucially: **an album with no segments and no GPS still clusters fine** on the
temporal path alone. Segments and GPS improve the result; neither is required.
This is the [genericity guard](../reference/folder-structure.md) — enforce it in tests (see
[build step 7](../build/build-order.md)).
