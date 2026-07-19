---
type: Pipeline Stage
title: ingest
description: Import an album's photos into a dedicated Immich album (manual, once per album).
tags: [stage, immich, ingest, manual]
timestamp: 2026-07-12
---

# `ingest` — Immich (manual, once per album)

Import the album's photos into a dedicated Immich album. Immich extracts EXIF
(timestamp, camera model, GPS when present) and runs its ML jobs automatically.
**GPS is optional, never assumed** — the honeymoon album lacks it; other albums
may have it.

In practice the `photobook-generator generate` CLI automates this
([single-command ingestion](../build/single-command-ingestion.md)). By default it
**uploads** a copy of each photo into a new Immich album; `--ingest-mode external`
instead registers the folder as a read-only Immich **external library** scanned in
place — no second copy, at the cost of keeping the originals mounted for the life of
the album ([external-library ingestion](../build/external-library-ingestion.md)).
Either way the result is the same: a dedicated Immich album the pipeline reads.

Downstream, [`sync`](./sync.md) is the boundary that pulls this into our DB.
