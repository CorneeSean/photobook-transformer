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

Downstream, [`sync`](./sync.md) is the boundary that pulls this into our DB.
