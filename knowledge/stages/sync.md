---
type: Pipeline Stage
title: sync
description: The boundary — pull Immich asset list, EXIF, duplicate groups, and faces/people into our DB, scoped by album_id.
tags: [stage, custom, immich, boundary]
timestamp: 2026-07-12
---

# `sync` — custom

Pull the album's asset list + EXIF + duplicate-group IDs from the Immich API into
our [DB](/data-model.md), tagged with `album_id`. **This is the boundary**:
everything downstream reads from our DB, never from Immich directly, so the rest
of the pipeline is independent of Immich's existence.

`sync` normalizes each photo's capture time to **UTC** (using the EXIF offset if
Immich exposes one; otherwise assuming `Album.timezone`, flagging photos where the
zone is ambiguous) — see [Open questions](/open-questions.md) #3.

`sync` also pulls **faces/people** per asset (`GET /faces?id=`, one call per
asset) into `Photo.face_count`/`Photo.person_ids` and upserts an album-scoped
`Person` cache — see [Immich enrichment](/immich-enrichment.md) and the
[data model](/data-model.md). Like the ambiguous-timestamp path, a face fetch that
fails for one asset is isolated: it's logged and recorded in `SyncResult`, leaving
that photo's face columns NULL rather than aborting the album. An album with no
recognized faces lands `person_ids=[]`, and every downstream people-aware stage
reduces to its face-free behavior (genericity guard).

> **Open question to resolve here:** does Immich's API expose the CLIP embeddings
> it computes for Smart Search, or only duplicate groups and search results? If
> not exposed, compute them independently via `open_clip` — cheap either way, no
> training. Resolve *before* building [`quality`](./quality.md), since several
> downstream stages depend on embeddings existing. See
> [Open questions](/open-questions.md) #1.

Feeds [`quality`](./quality.md) and every stage after it.
